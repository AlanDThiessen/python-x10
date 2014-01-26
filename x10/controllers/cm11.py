import logging
import Queue
import serial
import threading
import time

from .abstract import SerialX10Controller, X10Controller
from x10.devices.notifier import Notifier
from ..utils import *
from x10.protocol import functions

logger = logging.getLogger(__name__)
logging.basicConfig( level=logging.DEBUG,
                     format='%(asctime)s.%(msecs)03d %(name)-12s %(levelname)-8s %(message)s',
                     datefmt='%Y-%m-%d %H:%M:%S' )

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
POLL_EEPROM_ADDRESS     = 0x5B      # Two bytes following indicate address of timer/macro
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
            message = self.get( True, 4.6 )
            return message
        except Queue.Empty:
            logger.debug( "Message Queue Empty!" )
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
        logger.debug( "********** Starting Exit transaction **********" )
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
        self.houseCode      = 0
        self.numAttempts    = 0
        self.currentUnit    = 0
        self.checksum       = 0

    def Start(self):
        logger.debug( "********** Starting Command Transaction **********" )
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
        logger.debug( "   State Validate Checksum" )

        if( checksum == self.checksum ):
            logger.debug( "   Checksum Valid" )
            # Since checksum matches, reset the number of attempts
            # and send Ok for Transmission
            #self.numAttempts = 0
            self.controller.write( XMIT_OK )
            self.state = self.STATE_WAIT_READY
        else:
            logger.debug( "   Checksum Invalid" )
            # Checksum mistmach, increment number of attempts and try again
            self.numAttempts += 1
            
            if( self.numAttempts > self.MAX_ATTEMPTS ):
                logger.debug( "   Max attempts reached" )
                self.state = self.STATE_COMPLETE
            else:
                if( self.action == self.ACTION_ADDRESS ):
                    self.AddressUnit()
                elif( self.action == self.ACTION_FUNCTION ):
                    self.SendFunction()
                    
    def StateWaitReady(self, response):
        logger.debug( "   State Wait Ready" )

        if( response == INTERFACE_READY ):
            logger.debug( "   Interface ready" )

            if( self.action == self.ACTION_ADDRESS ):
                # Increment the current unit
                self.currentUnit += 1
                
                # See if we need to address another unit.
                # If not, then send the function
                if( not self.AddressUnit() ):
                    self.SendFunction()
                    
                self.state = self.STATE_VALIDATE_CHECKSUM
                
            elif( self.action == self.ACTION_FUNCTION ):
                self.controller.NotifyCommandCode( self.function, self.houseCode, self.amount, self.units )
                self.state = self.STATE_COMPLETE
        else:
            logger.debug( "   Device Not Ready" )
            # Checksum mistmach, increment number of attempts and try again
            self.numAttempts += 1

            if( self.numAttempts > self.MAX_ATTEMPTS ):
                logger.debug( "   Max attempts reached" )
                self.state = self.STATE_COMPLETE

    def AddressUnit(self):
        if( self.currentUnit < len( self.units ) ):
            logger.debug( "   Address unit: %s", self.units[self.currentUnit] )
            
            self.action = self.ACTION_ADDRESS
            x10Addr = ( encodeX10HouseCode( self.units[self.currentUnit][0], self.controller ) << 4 ) | encodeX10UnitCode( self.units[self.currentUnit][1:], self.controller )
            self.controller.write( HD_SEL )
            self.controller.write( x10Addr )
            self.checksum = ( HD_SEL + x10Addr ) & 0x00FF
            
            return True
        else:
            return False
        
    def SendFunction(self):
        logger.debug( "   Send Function: %s", encodeFunctionName( self.function, self.controller ) )
        self.action = self.ACTION_FUNCTION
        
        self.houseCode = self.units[0][0]
        
        cmd = (encodeX10HouseCode(self.houseCode, self.controller) << 4) | self.function

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
    
    def __init__(self, controller, initialByte=None):
        Transaction.__init__(self, controller)
        logger.debug( "New poll transaction: 0x%02X", initialByte )
        self.readSize = None
        self.data = []
        self.data.append(initialByte)

        if( initialByte == POLL_EEPROM_ADDRESS ):
            self.readSize = 3   # This byte plus two following equals three total
            logger.debug( "Poll transaction: initial readSize: %d", self.readSize)
        
    def Start(self):
        logger.debug( "********** Starting Poll Transaction **********" )
        self.complete = False
        
        # If it's a normal poll request, we need to respond, and the readSize
        # will be the next byte read. 
        if( self.data[0] == POLL_REQUEST):
            self.controller.write( POLL_RESPONSE )
            
        return True
    
    def HandleMessage(self, message):
        if( ( message.id == QueueMessage.MSG_READ ) and ( len( message.data ) >= 1 ) ):
            if( self.readSize == None ):
                self.readSize = message.data[0] + 1 # Need to account for the initial header byte
                logger.debug( "Poll transaction: readSize: %d", self.readSize)
                
                if( self.readSize > MAX_READ_BYTES ):
                    self.readSize = MAX_READ_BYTES
                    
                if( self.readSize == 0 ):
                    self.complete = True
                    
            else:
                self.data.append( message.data[0] )

                if( ( self.readSize != None ) and ( len( self.data ) >= self.readSize ) ):
                    self.complete = True

                if( len( self.data ) >= self.readSize ):
                    self.controller.DecodeData( self.data )
        else:
            logger.debug( "Poll transaction: Read Timeout")
            self.complete = True

    def IsComplete(self):
        if( self.complete ):
            logger.debug( "poll transaction complete" )
            return True
        else:
            return False


class ClockTransaction(Transaction):
    def Start(self):
        logger.debug( "********** Starting Clock Transaction **********" )
        # For now, just send the header to shut up the device
        self.controller.write( POLL_PF_RESP )
        time.sleep( 0.010 )
        return True


class MacroTransaction(Transaction):
    def Start(self):
        logger.debug( "********** Starting Macro Transaction **********" )
        return True


class StatusRequestTransaction(Transaction):
    
    def __init__(self, controller, event):
        Transaction.__init__(self, controller, event)
        logger.debug( "New status transaction" )
        
    def Start(self):
        logger.debug( "********** Starting Status Request Transaction **********" )
        self.readSize = STAUS_REQUEST_NUM_BYTES
        self.data = []
        self.controller.write( STATUS_REQUEST )
        return True
    
    def HandleMessage(self, message):
        if( ( message.id == QueueMessage.MSG_READ ) and ( len( message.data ) >= 1 ) ):
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
    
                if( ( data == POLL_REQUEST ) or ( data == POLL_EEPROM_ADDRESS ) ):
                    logger.debug( "   creating poll transaction")
                    transaction = PollTransaction( self.controller, data )
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
        logger.debug( "Process thread started" )
        
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
                
        logger.debug( "Process thread ending" )
        

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
        self.lastUnits = []
        self.notifiers = []
        
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
        logger.debug( "Closing device..." )
        # Closing the serial port should cause the read thread to terminate
        SerialX10Controller.close(self)
        
        # Send an exit
        exit = ExitTransaction(self)
        self.transactions.AddTransaction( TransactionQueue.PRI_EXIT, exit )
        
        # Now, wait for the threads to complete
        logger.debug( "Waiting for threads to end..." )
        self.readThread.join()
        logger.debug( "   read thread done" )
        self.processThread.join()
        logger.debug( "   process thread done" )
    
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
            logger.debug( "Waiting for event..." )
            event.wait()
            logger.debug( "Event Done!" )

    def AddNotifier(self, notifier):
        self.notifiers.append( notifier )
        
    def StatusRequest(self):
        self.do( functions.STATREQ )
        
    def StatusResponse(self, data):
        logger.debug( "Status Response: ..." )

    def NotifyMacroExecuted(self, data):
        logger.info( "Macro Executed: Address: 0x%02X%02X", data[0], data[1] )
    
    def NotifyCommandCode(self, functionCode, houseCode, amount, units ):
        functionName = encodeFunctionName( functionCode, self )

        # If there are unit codes specified, then we need to remember the last
        # units operated on
        if( len( units ) != 0 ):
            self.lastUnits = units

        # Go through the notifiers and send notification
        for notifier in self.notifiers:
            notifier.Notify( houseCode, functionCode, functionName, amount, self.lastUnits )

        if( ( functionCode == functions.DIM ) or ( functionCode == functions.BRIGHT ) ):
            percentage = round( amount * 100 / DIM_BRIGHT_MAX )
            logger.info( "Command: Unit(s): %s, House: %1s, Function: %s, Amount: %.0f%%", ', '.join( map( str, self.lastUnits ) ), houseCode, functionName, percentage )
        else:
            logger.info( "Command: Unit(s): %s, House: %1s, Function: %s", ', '.join( map( str, self.lastUnits ) ), houseCode, functionName )
        
    def DecodeData(self, data):
        initialByte = data.pop(0)
        
        if( initialByte == POLL_EEPROM_ADDRESS ):
            self.NotifyMacroExecuted( data )
        elif( initialByte == POLL_REQUEST ):
            # First, get the Function/Address mask from the first byte
            index = 0
            funcAddrMask = data[index]
            index += 1
            
            # The size is the number of bytes remaining
            size = len( data ) - 1
            
            functionCode = 0
            houseCode = 0
            amount = 0
            unitCode = []
            
            try:
                while size:
                    # If the bit in the mask is a 1, the corresponding data byte is a function
                    # If the bit in the mask is a 0, the corresponding data byte is a house code
                    if( funcAddrMask & 0x01 ):
                        # The function code is in the lower nibble
                        functionCode = data[index] & 0x0F
                        # The house code is in the upper nibble
                        houseCode = decodeX10HouseCode( data[index] >> 4, self )
                        
                        # If it's a bright or dim command, then there is another byte listing the amount
                        if( ( functionCode == functions.DIM ) or ( functionCode == functions.BRIGHT ) ):
                            index += 1
                            size -= 1
                            amount = data[index]
    
                        self.NotifyCommandCode( functionCode, houseCode, amount, unitCode )
                        
                        # Reinitialize since there could be more in the buffer
                        amount = 0
                        unitCode = []
                            
                    else:
                        unitCode.append( decodeX10Address( data[index], self ) )
                        
                    funcAddrMask = funcAddrMask >> 1
                    index += 1
                    size -= 1
            except:
                logger.debug( "Decode Failure" )
