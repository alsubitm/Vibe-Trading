"""Verify an explicit one-minute session grid; never count synthetic data as real acceptance."""
from datetime import date, timedelta

from ..domain.base import DomainModel, UTCTimestamp
from ..config.models import FreshnessConfig
from .clock import MarketSession
from .interfaces import HistoricalDataProvider
from .validation import DataIntegrityError, validate_snapshot


class SessionCoverage(DomainModel):
    expected_points: int
    valid_points: int
    real_points: int
    missing_or_invalid: tuple[UTCTimestamp, ...]

    @property
    def real_session_passed(self) -> bool:
        return self.expected_points > 0 and self.real_points == self.expected_points


def assess_session(provider: HistoricalDataProvider, underlying: str, expiration: date,
                   session: MarketSession, freshness: FreshnessConfig | None = None) -> SessionCoverage:
    """Check minute closes from open+1m through close, using caller-verified bounds.

    Each point must provide a complete fresh chain and that minute's completed
    1m bar. REAL is source-declared provenance, not proof of licensing or an
    independent certification that source data are accurate.
    """
    timestamp = session.opens_at + timedelta(minutes=1)
    expected = valid = real = 0
    missing = []
    while timestamp <= session.closes_at:
        expected += 1
        try:
            record = provider.snapshot_at(underlying, expiration, timestamp)
            validate_snapshot(record, timestamp, freshness or FreshnessConfig())
            if record.chain.underlying != underlying or record.chain.expiration != expiration:
                raise DataIntegrityError("provider returned a different symbol/expiration")
            if not any(b.bar.timeframe == "1m" and b.bar.timestamp == timestamp - timedelta(minutes=1)
                       and b.available_at <= timestamp for b in record.bars):
                raise DataIntegrityError("DATA_UNAVAILABLE: missing completed minute bar")
            valid += 1
            real += int(record.data_kind == "REAL")
        except DataIntegrityError:
            missing.append(timestamp)
        timestamp += timedelta(minutes=1)
    return SessionCoverage(expected_points=expected, valid_points=valid, real_points=real,
                           missing_or_invalid=tuple(missing))
