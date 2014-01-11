import logging
import Queue
import serial
import threading
import time

from .abstract import SerialX10Controller, X10Controller
from ..utils import *
from x10.protocol import functions

logger = logging.getLogger(__name__)

CM11_BAUD       = 4800

# Transmit Constants
HD_SEL                  = 0x04      # Header byte to address a device
HD_FUN                  = 0x06      # Header byte to send a function command
XMIT_OK                 = 0x00      # Ok for Transmission
MAX_DIM                 = 22        # Maximum Dim/Bright amount
POLL_RESPONSE           = 0xC3      # Respond to a device's poll request
STATUS_REQUEST          = 0x8B      # Request status from the device
POLL_PF_RESP            = 0x9B      # Respond to power fail poll

# Receive Constants
POLL_REQUEST            = 0x5A      # Poll request from CM11A
POLL_POWER_FAIL         = 0xA5      # Poll request indicating power fail
INTERFACE_READY         = 0x55      # The interface is ready
MAX_READ_BYTES          = 9         # The maximum number of bytes in the buffer after the size
STAUS_REQUEST_NUM_BYTES = 14        # The number of bytes to read for a status request
DIM_BRIGHT_MAX          = 210


class TransactionQueue(Queue.PriorityQueue):
    # Transaction Queue priorities
    ( PRI_EXIT,
      PRI_IMMEDIATE,
      PRI_NORMAL
    ) = range(3)
    
    def __init__(self):
        Queue.PriorityQueue.__init__(self)
    
    def AddTransaction(self, priority, transaction):
        self.put( ( priority, transaction ) )
        
    def GetTransaction(self):
        (priority, transaction) = self.get()
        return transaction   


class MessageQueue(Queue.Queue):
    
    def AddMessage(self, message):
        self.put( message )
        
    def GetMessage(self):
        try:
            message = self.get( True, 3 )
            return message
        except Queue.Empty:
            print "Message Queue Empty!"
            return None
        except:
            return None
        
    def Clear(self):
        logger.debug( "Clearing message queue" )
        while not self.empty():
            message = self.get( False )
            self.task_done()


# Classes to handle transactions
# This is the base class
class Transaction(object):
    def __init__(self, controller, event=None):
        self.controller = controller
        self.event = event
        
    def Start(self):
        """
        Start the Transaction
        """
        raise NotImplementedError()
    
    def HandleMessage(self, message):
        """
        Process a message from the serial port
        """
        raise NotImplementedError()
    
    def IsComplete(self):
        """
        Returns whether the transaction is complete or not
        """
        return True
    
    def SignalComplete(self):
        if( self.event ):
            self.event.set()

class ExitTransaction(Transaction):
    def Start(self):
        print "Starting exit transition"
        return False

class CommandTransaction(Transaction):
    ( ACTION_ADDRESS,
      ACTION_FUNCTION
    ) = range(2)
    
    ( STATE_VALIDATE_CHECKSUM,
      STATE_WAIT_READY,
      STATE_COMPLETE
    ) = range(3)
    
    MAX_ATTEMPTS = 5
    
    def __init__(self, controller, function, units, amount, event):
        Transaction.__init__(self, controller, event)
        self.units          = units
        self.function       = function
        self.amount         = amount
        self.numAttempts    = 0
        self.currentUnit    = 0
        self.checksum       = 0

    def Start(self):
        if( self.AddressUnit() ):
            self.state = self.STATE_VALIDATE_CHECKSUM
        else:
            self.state = self.STATE_COMPLETE
            
        return True
        
    def HandleMessage(self, message):
        data = -1

        if( ( message.id == QueueMessage.MSG_READ ) and ( len( message.data ) >= 1 ) ):
            data = message.data[0]
            
        if  ( self.state == self.STATE_VALIDATE_CHECKSUM ):
            self.StateValidateChecksum( data )
        elif( self.state == self.STATE_WAIT_READY        ):
            self.StateWaitReady( data )
        
    def IsComplete(self):
        return( self.state == self.STATE_COMPLETE )

    def StateValidateChecksum(self, checksum):
        print "   State Validate Checksum"

        if( checksum == self.checksum ):
            print "   Checksum Valid"
            # Since checksum matches, reset the number of attempts
            # and send Ok for Transmission
            self.numAttempts = 0
            self.controller.write( XMIT_OK )
            self.state = self.STATE_WAIT_READY
        else:
            print "   Checksum Invalid"
            # Checksum mistmach, increment number of attempts and try again
            self.numAttempts += 1
            
            if( self.numAttempts > self.MAX_ATTEMPTS ):
                print "   Max attempts reached"
                self.state = self.STATE_COMPLETE
            else:
                if( self.action == self.ACTION_ADDRESS ):
                    self.AddressUnit()
                elif( self.action == self.ACTION_FUNCTION ):
                    self.SendFunction()
                    
    def StateWaitReady(self, response):
        print "   State Wait Ready"                    

        if( response == INTERFACE_READY ):
            print "   Interface ready"

            if( self.action == self.ACTION_ADDRESS ):
                # Increment the current unit
                self.currentUnit += 1
                
                # See if we need to address another unit.
                # If not, then send the function
                if( not self.AddressUnit() ):
                    self.SendFunction()
                    
                self.state = self.STATE_VALIDATE_CHECKSUM
                
            elif( self.action == self.ACTION_FUNCTION ):
                self.state = self.STATE_COMPLETE
        else:
            print "   Device Not Read"
            # Checksum mistmach, increment number of attempts and try again
            self.numAttempts += 1

            if( self.numAttempts > self.MAX_ATTEMPTS ):
                print "   Max attempts reached"
                self.state = self.STATE_COMPLETE
            else:
                self.state = self.STATE_VALIDATE_CHECKSUM
                if( self.action == self.ACTION_ADDRESS ):
                    self.AddressUnit()
                elif( self.action == self.ACTION_FUNCTION ):
                    self.SendFunction()

    def AddressUnit(self):
        if( self.currentUnit < len( self.units ) ):
            print "   Address unit: ", self.units[self.currentUnit]
            
            self.action = self.ACTION_ADDRESS
            x10Addr = ( encodeX10HouseCode( self.units[self.currentUnit][0], self.controller ) << 4 ) | encodeX10UnitCode( self.units[self.currentUnit][1:], self.controller )
            self.controller.write( HD_SEL )
            self.controller.write( x10Addr )
            self.checksum = ( HD_SEL + x10Addr ) & 0x00FF
            
            return True
        else:
            return False
        
    def SendFunction(self):
        print "   Send Function: ", self.function
        self.action = self.ACTION_FUNCTION
        
        cmd = (encodeX10HouseCode(self.units[0][0], self.controller) << 4) | self.function

        # For the BRIGHT and DIM Commands, need to or in the amount to dim
        # into the top 5 bits of the header
        header = HD_FUN
        if self.amount > MAX_DIM:
            self.amount = MAX_DIM
            
        if( ( self.function == functions.DIM    ) or
            ( self.function == functions.BRIGHT ) ):
            header |= (self.amount << 3)
        
        self.checksum = ( header + cmd ) & 0x00FF
        self.controller.write( header )
        self.controller.write( cmd )


class PollTransaction(Transaction):
    
    def __init__(self, controller):
        Transaction.__init__(self, controller)
        logger.debug( "New poll transaction" )
        
    def Start(self):
        logger.debug( "Poll transaction starting")
        self.readSize = None
        self.data = []
        self.controller.write( POLL_RESPONSE )
        return True
    
    def HandleMessage(self, message):
        if( self.readSize == None ):
            self.readSize = message.data[0]
            logger.debug( "Poll transaction: readSize: %d", self.readSize)
            
            if( self.readSize > MAX_READ_BYTES ):
                self.readSize = MAX_READ_BYTES
                
        else:
            self.data.append( message.data[0] )
            
            if( len( self.data ) >= self.readSize ):
                self.controller.DecodeData( self.data )

    def IsComplete(self):
        if( ( self.readSize != None ) and ( len( self.data ) >= self.readSize ) ):
            logger.debug( "poll transaction complete" )
            return True
        else:
            return False


class ClockTransaction(Transaction):
    def Start(self):
        print "Clock transaction starting"
        # For now, just send the header to shut up the device
        self.controller.write( POLL_PF_RESP )
        time.sleep( 0.010 )
        return True


class MacroTransaction(Transaction):
    def Start(self):
        return True


class StatusRequestTransaction(Transaction):
    
    def __init__(self, controller, event):
        Transaction.__init__(self, controller, event)
        logger.debug( "New status transaction" )
        
    def Start(self):
        logger.debug( "Status transaction starting")
        self.readSize = STAUS_REQUEST_NUM_BYTES
        self.data = []
        self.controller.write( STATUS_REQUEST )
        return True
    
    def HandleMessage(self, message):
            self.data.append( message.data[0] )
            
            if( len( self.data ) >= self.readSize ):
                self.controller.StatusResponse( self.data )

    def IsComplete(self):
        if( ( self.readSize != None ) and ( len( self.data ) >= self.readSize ) ):
            logger.debug( "Status transaction complete" )
            return True
        else:
            return False


# Classes to handle threading
class QueueMessage():
    ( MSG_UNDEFINED,
      MSG_EXIT,
      MSG_READ,
      MSG_WRITE,
      MSG_TIMEOUT
    ) = range(5)
    
    def __init__(self, id=MSG_UNDEFINED):
        self.id     = id
        self.data   = bytearray()


class ThreadSerialRead(threading.Thread):
    ( STATE_EXIT,
      STATE_BYTE,
    ) = range(2)

    def __init__(self, msgQueue, transQueue, controller):
        threading.Thread.__init__(self)
        self.msgQueue   = msgQueue
        self.transQueue = transQueue
        self.controller = controller
        
    def run(self):
        logger.debug(  "Read thread started" )
        state = self.STATE_BYTE
        
        while state != self.STATE_EXIT:
            state = self.ReadByte()
                
        logger.debug( "Read thread exiting" )
        
    def ReadByte(self):
        newState = self.STATE_BYTE
        
        try:
            data = self.controller.read()

            if data != "":

                if( data == POLL_REQUEST ):
                    logger.debug( "   creating poll transaction")
                    transaction = PollTransaction( self.controller )
                    self.transQueue.AddTransaction( TransactionQueue.PRI_IMMEDIATE, transaction )
                    
                elif( data == POLL_POWER_FAIL ):
                    logger.debug( "   creating power fail transaction")
                    transaction = ClockTransaction( self.controller )
                    self.transQueue.AddTransaction( TransactionQueue.PRI_IMMEDIATE, transaction )
                    
                else:
                    msg = QueueMessage( QueueMessage.MSG_READ )
                    msg.data.append( data )
                    self.msgQueue.AddMessage( msg )
                    
        except:
            newState = self.STATE_EXIT
            
        return newState

    
class ThreadProcessTransactions(threading.Thread):
    STATE_EXIT      = 0
    STATE_RUN       = 1

    def __init__(self, msgQueue, transQueue, controller):
        threading.Thread.__init__(self)
        self.msgQueue   = msgQueue
        self.transQueue = transQueue
        self.controller = controller
        
    def run(self):
        print "Process thread started"
        
        # The CM11A device sends the Power Fail poll every second after initial
        # power-up, and will not respond to commands until that poll is satisfied.
        # This sleep ensures the read thread has an opportunity to see the poll
        # and send a high-priority Power Fail transaction, ensuring this is the
        # first transaction processed.
        time.sleep( 1.5 )
        state = self.STATE_RUN
        
        while( state == self.STATE_RUN ):
            # Suspend on the queue until a transaction arrives
            transaction = self.transQueue.GetTransaction()
            
            # First, reset the message queue
            self.msgQueue.Clear()
            
            # Start the transaction: The special exit transaction returns false at start
            run = transaction.Start()

            if( run ):
                while( not transaction.IsComplete() ):
                    message = self.msgQueue.GetMessage()
                    if( message == None ):
                        message = QueueMessage( QueueMessage.MSG_TIMEOUT )
                    transaction.HandleMessage( message )
                transaction.SignalComplete()
            else:
                state = self.STATE_EXIT
                
        print "Process thread ending"
        

class CM11(SerialX10Controller):
    # -----------------------------------------------------------
    # House and unit code table
    # -----------------------------------------------------------
    HOUSE_ENCMAP = {
        "A": 0x6,
        "B": 0xE,
        "C": 0x2,
        "D": 0xA,
        "E": 0x1,
        "F": 0x9,
        "G": 0x5,
        "H": 0xD,
        "I": 0x7,
        "J": 0xF,
        "K": 0x3,
        "L": 0xB,
        "M": 0x0,
        "N": 0x8,
        "O": 0x4,
        "P": 0xC
        }
    
    UNIT_ENCMAP = {
        "1": 0x6,
        "2": 0xE,
        "3": 0x2,
        "4": 0xA,
        "5": 0x1,
        "6": 0x9,
        "7": 0x5,
        "8": 0xD,
        "9": 0x7,
        "10": 0xF,
        "11": 0x3,
        "12": 0xB,
        "13": 0x0,
        "14": 0x8,
        "15": 0x4,
        "16": 0xC
        }
    
    def __init__(self, aDevice, suspend=False):
        SerialX10Controller.__init__(self, aDevice, CM11_BAUD)
        # The queue to use for communication
        self.messages = MessageQueue()
        # The queue to use for transactions
        self.transactions = TransactionQueue()
        self.suspend = suspend
        
    def open(self):
        # Open the serial port
        SerialX10Controller.open(self, 1)

        # Start the thread for reading 
        self.readThread = ThreadSerialRead( self.messages, self.transactions, self )
        self.readThread.start()
        
        # Start the thread for processing/writing
        self.processThread = ThreadProcessTransactions( self.messages, self.transactions, self )
        self.processThread.start()
        
    def close(self):
        print "Closing device..."
        # Closing the serial port should cause the read thread to terminate
        SerialX10Controller.close(self)
        
        # Send an exit
        exit = ExitTransaction(self)
        self.transactions.AddTransaction( TransactionQueue.PRI_EXIT, exit )
        
        # Now, wait for the threads to complete
        print "Waiting for threads to end..."
        self.readThread.join()
        print "   read thread done"
        self.processThread.join()
        print "   process thread done"
    
    def ack(self):
        return True

    def do(self, function, units=None, amount=None ):
        event = None
        
        if( self.suspend ):
            event = threading.Event()
        
        if( function == functions.STATREQ ):
            transaction = StatusRequestTransaction( self, event )
        else:
            transaction = CommandTransaction( self, function, units, amount, event )
            
        self.transactions.AddTransaction( TransactionQueue.PRI_NORMAL, transaction )
        
        if( self.suspend ):
            print "Waiting for event..."
            event.wait()
            print "Event Done!"
    
    def StatusRequest(self):
        self.do( functions.STATREQ )
        
    def StatusResponse(self, data):
        print "Status Response: ", data

    def DecodeData(self, data):
        # First, get the Function/Address mask from the first byte
        index = 0
        funcAddrMask = data[index]
        index += 1
        
        # The size is the number of bytes remaining
        size = len( data ) - 1
        
        try:
            while size:
                # If the bit in the mask is a 1, the corresponding data byte is a function
                # If the bit in the mask is a 0, the corresponding data byte is a house code
                if( funcAddrMask & 0x01 ):
                    functionCode = data[index] & 0x0F
                    functionName = encodeFunctionName( functionCode, self )
                    houseCode = decodeX10HouseCode( data[index] >> 4, self )
                    
                    if( ( functionCode == functions.DIM ) or ( functionCode == functions.BRIGHT ) ):
                        index += 1
                        size -= 1
                        amount = round( data[index] * 100 / DIM_BRIGHT_MAX )
                        logger.debug( "House: %1s, Function: %s (%d), Amount: %.0f%%", houseCode, functionName, functionCode, amount )
                    else:
                        logger.debug( "House, %1s, Function: %s (%d)", houseCode, functionName, functionCode )
                        
                else:
                    unitCode = decodeX10Address( data[index], self )
                    logger.debug( "Unit: %s", unitCode )
                    
                funcAddrMask = funcAddrMask >> 1
                index += 1
                size -= 1
        except:
            logger.debug( "Decode Failure" )
