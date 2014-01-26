
class Notifier(object):
    """
    Abstract class for call-back notification of events that happen on the
    X10 house network.
    """
    def Notify(self, houseCode, functionCode, functionName, amount, units ):
        """
        Functionality to run when an event occurs on the X10 house network.
        Parameters:
            houseCode - The house code the units were on (e.g. A..N)
            functionCode - The function code that was run
            functionName - The name of the function that was run
            amount - If bright or dim command, the amount
            units - Array of unit codes (e.g. A1,A2) included in the command
        """
        raise NotImplementedError()
