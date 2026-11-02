from enum import Enum


class DTEBucket(str, Enum):
    ZERO_DTE = "0dte"
    ONE_DTE = "1dte"
    TWO_THREE_DTE = "2_3dte"
    FOUR_SEVEN_DTE = "4_7dte"


class SessionPhase(str, Enum):
    PREMARKET = "PREMARKET"
    OPENING = "OPENING"
    MORNING = "MORNING"
    MIDDAY = "MIDDAY"
    AFTERNOON = "AFTERNOON"
    POWER_HOUR = "POWER_HOUR"
    POSTMARKET = "POSTMARKET"
    CLOSED = "CLOSED"
