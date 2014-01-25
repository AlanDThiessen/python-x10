# This file is a utility script for sending individual commands to an
# X10 device.

import sys
import os
import re
import logging
import time

from decimal import Decimal
from x10.controllers.cm11 import CM11

OPTIONS = { 'logLevel':  'WARNING'
          }

#            Command    # Args regex
COMMANDS = { 'TURN':    "(ON|OFF)",
             'DIM':     '(\d+|ALL)',
             'BRIGHT':  '(\d+|ALL)'
           }

execute = { 'command':   '',
            'devices':   [],
            'args':      []
          }

command = []


def ParseArgs():
    for arg in sys.argv[1:]:
        if( arg[0:2] == '--' ):
            (left, right) = arg[2:].split( '=', 2 )
            if( left == 'log' ):
                OPTIONS['logLevel'] = right.upper()
        else:
            command.append(arg.upper())

def ValidateArgs():
    # First check and set the log level
    num = getattr( logging, OPTIONS['logLevel'] )
    if not isinstance( num, int ):
        raise ValueError( 'Invalid log level: %s' % OPTIONS['logLevel'] )
    logging.basicConfig( level=num )
    
    # Go through all commands
    if( len( command ) > 2 ):
        if( command[0].upper() in COMMANDS.keys() ):
            execute['command'] = command.pop(0)
        else:
            raise ValueError( 'Invalid command: %s' % command[0] )
        
        # Now get the devices
        if( command[0] ):
            for device in command.pop(0).split(','):
                m = re.search( r"^([A-N])(\d{1,2})", device )
                if( m ):
                    unit = Decimal( m.group(2) )
                    if( ( unit >= 1 ) and ( unit <= 16 ) ):
                        execute['devices'].append( device )
                    else:
                        raise ValueError( 'Invalid unit code: %s' % unit )
                else:
                    raise ValueError( 'Invalid device code: %s' % OPTIONS['device'] )
                
        # Now get the rest of the arguments
        for arg in command:
            m = re.search( COMMANDS[execute['command']], arg )
            if( m ):
                execute['args'].append( m.group(1) )
    else:
        raise ValueError( 'Please specify a valid command' )


def RunCommand():
    # Open the device
    device = CM11( '/dev/ttyUSB0', True )
    device.open()

    module = device.actuator()

    for dev in execute['devices']:
        print 'dev: ', dev
        module.addUnit( dev )

    print 'command: ', execute['command']
    print 'args: ', execute['args'][0]

    if( execute['command'] == 'TURN' ):
        if( execute['args'][0] == 'ON' ):
            module.on()
        elif( execute['args'][0] == 'OFF' ):
            module.off()    
    elif( execute['command'] == 'DIM' ):
        module.dim( int( execute['args'][0] ) )
    elif( execute['command'] == 'BRIGHT' ):
        module.bright( int( execute['args'][0] ) )
    
#    device.StatusRequest()
    time.sleep( 10 )
    
    device.close()
    

ParseArgs()
ValidateArgs()
RunCommand()

