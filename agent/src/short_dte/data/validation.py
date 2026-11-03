"""Fail-closed integrity checks with an injected reference time."""
from datetime import datetime

from ..config.models import FreshnessConfig, HistoricalDataConfig
from ..domain.base import as_utc
from .clock import require_fresh
from .models import HistoricalSnapshot


class DataIntegrityError(ValueError):
    """Caller must produce NO_NEW_TRADES, never silently fall back to synthetic data."""


class DataUnavailableError(DataIntegrityError):
    pass


def validate_snapshot(snapshot: HistoricalSnapshot, as_of: datetime,
                      freshness: FreshnessConfig, *, require_complete: bool = True) -> None:
    as_of = as_utc(as_of)
    if snapshot.available_at > as_of:
        raise DataIntegrityError("FUTURE_DATA: snapshot unavailable at reference time")
    if require_complete and not snapshot.chain.is_complete:
        raise DataIntegrityError("INCOMPLETE_CHAIN")
    try:
        require_fresh(snapshot.chain.timestamp, as_of, freshness.chain_snapshot_seconds)
        require_fresh(snapshot.underlying_quote.timestamp, as_of, freshness.underlying_quote_seconds)
        for quote in snapshot.chain.contracts:
            require_fresh(quote.timestamp, as_of, freshness.option_quote_seconds)
    except ValueError as exc:
        raise DataIntegrityError(str(exc)) from exc


def validate_sequence(records: tuple[HistoricalSnapshot, ...], freshness: FreshnessConfig,
                      limits: HistoricalDataConfig | None = None) -> None:
    last = {}
    for record in records:
        key = (record.chain.underlying, record.chain.expiration)
        if key in last and record.chain.timestamp <= last[key]:
            raise DataIntegrityError("duplicate or non-monotonic snapshot timestamps")
        last[key] = record.chain.timestamp
        # Event-time freshness on import; publication visibility is separately
        # enforced by the provider at retrieval, never replaced by today's clock.
        if record.underlying_quote.timestamp > record.chain.timestamp:
            raise DataIntegrityError("FUTURE_DATA")
        try:
            require_fresh(record.underlying_quote.timestamp, record.chain.timestamp,
                          freshness.underlying_quote_seconds)
            for quote in record.chain.contracts:
                require_fresh(quote.timestamp, record.chain.timestamp, freshness.option_quote_seconds)
                if quote.implied_volatility is not None and quote.implied_volatility > (
                        limits or HistoricalDataConfig()).maximum_implied_volatility:
                    raise ValueError("IV_ANOMALY")
        except ValueError as exc:
            raise DataIntegrityError(str(exc)) from exc
