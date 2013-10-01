import serial

from .abstract import SerialX10Controller, X10Controller

from ..utils import encodeX10HouseCode, encodeX10UnitCode, encodeX10Address

from x10.protocol import functions

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
    
    # Transmit Constants
    HD_SEL          = 0x04      # Header byte to address a device
    HD_FUN          = 0x06      # Header byte to send a function command
    POLL_RESPONSE   = 0xC3      # Respond to a device's poll request
    POLL_PF_RESP    = 0xFB      # Respond to power fail poll
    
    # Receive Constants
    POLL_REQUEST    = 0x5A      # Poll request from CM11A
    POLL_POWER_FAIL = 0xA5      # Poll request indicating power fail
    
    def __init__(self, aDevice):
        X10Controller.__init__(self, aDevice)
        self._baudrate = 4800
        
    def open(self):
        SerialX10Controller.open(self)
        while True:
            data = self.read()
            if data == "":
                break
    
    # Override the read command.  Occasionally, a Poll message might be
    # be received.  The CM11A does not like it if the computer does not respond
    # to the poll.  Then read another byte.
    def read(self):
        data = SerialX10Controller.read(self)
        if data == self.POLL_POWER_FAIL:
            self.write(self.POLL_PF_RESP)
            data = SerialX10Controller.read(self)
        elif data == self.POLL_REQUEST:
            self.write(self.POLL_RESPONSE)
            data = SerialX10Controller.read(self)
        return( data )
    
    def ack(self):
        return True
    
    def actuator(self, x10addr, aX10ActuatorKlass=None):
        select = (encodeX10HouseCode(x10addr[0], self) << 4) | encodeX10UnitCode(x10addr[1:], self)
        checksum = 0x00
        
        while checksum != self.HD_SEL+select:
            self.write(self.HD_SEL)
            self.write(select)
            checksum = self.read()
        self.write(0x00)
        self.read()
        return SerialX10Controller.actuator(self, x10addr, aX10ActuatorKlass=aX10ActuatorKlass)
    
    def do(self, function, x10addr=None, amount=None):
        cmd = (encodeX10HouseCode(x10addr[0], self) << 4) | function
        checksum = 0x00
        
        while checksum != self.HD_FUN+cmd:
            self.write(self.HD_FUN)
            self.write(cmd)
            checksum = self.read()
        self.write(0x00)
        self.read()