"""Engine-wide exception types."""


class EngineError(Exception):
    """Base class for engine errors."""


class DataUnavailable(EngineError):
    """A data source could not deliver the requested data (network, parsing, empty)."""


class StaleData(EngineError):
    """Data is older than the tolerance for the requested operation."""


class RiskLimitBreached(EngineError):
    """A hard risk rule (leverage cap, margin) would be violated."""


class AccountHalted(EngineError):
    """The account is halted (dead or circuit breaker) and cannot take new risk."""
