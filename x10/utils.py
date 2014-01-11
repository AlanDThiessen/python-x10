
def encodeX10HouseCode(house_code, anX10Controller):
    """
    Given an X10 House Code (e.g. "A"), return the value for this
    specific controller
    """
    return anX10Controller.HOUSE_ENCMAP[house_code]

def decodeX10HouseCode(value, anX10Controller):
    """
    Return the House Code associated with a given value
    """
    values = anX10Controller.HOUSE_ENCMAP.values()
    if value in values:
        index = values.index( value )
        return anX10Controller.HOUSE_ENCMAP.keys()[index]
    else:
        return None
  
def encodeX10UnitCode(unit_code, anX10Controller):
    """
    Given an X10 Unit Code (e.g. "6"), return the value for this
    specific controller
    """
    return anX10Controller.UNIT_ENCMAP[str(unit_code)]

def decodeX10UnitCode(value, anX10Controller):
    """
    Return the Unit Code associated with a given value
    """
    values = anX10Controller.UNIT_ENCMAP.values()
    if value in values:
        index = values.index( value )
        return anX10Controller.UNIT_ENCMAP.keys()[index]
    else:
        return None

def encodeX10Address(x10addr, controller):
    """
    Given an X10 Address (e.g. "B8"), return the value for this
    specific controller
    """
    res = encodeX10HouseCode(x10addr[0], controller) << 4
    res += encodeX10UnitCode(x10addr[1], controller)
    return res


def decodeX10Address(x10addr, controller):
    """
    Given a numeric value, return X10 Address as a string
    """
    houseCode = decodeX10HouseCode( x10addr >> 4, controller )
    deviceCode = decodeX10UnitCode( x10addr & 0x0F, controller )
    return houseCode + deviceCode


def encodeFunctionName(function, anX10Controller):
    """
    Given an X10 Function Code, return it's name
    """
    return anX10Controller.FUNCTION_NAMES[function]



