"""Local JSONL archives and an immutable in-memory point-in-time provider."""
from datetime import date, datetime
from bisect import bisect_right
from pathlib import Path
from zoneinfo import ZoneInfo

from ...config.models import FreshnessConfig, HistoricalDataConfig
from ...domain.base import as_utc
from ..models import HistoricalSnapshot
from ..clock import EXCHANGE_TIMEZONE
from ..normalizer import canonical_snapshot_json, parse_snapshot_json
from ..validation import DataUnavailableError, validate_sequence, validate_snapshot


def read_jsonl(path: Path, *, maximum_bytes: int = 128 * 1024 * 1024) -> tuple[HistoricalSnapshot, ...]:
    """Import one explicitly supplied UTF-8 canonical JSON object per line.

    No environment, credential discovery, remote URLs or implicit provider calls.
    The bounded file read rejects whole malformed batches before any persistence.
    """
    if maximum_bytes <= 0:
        raise ValueError("maximum_bytes must be positive")
    with Path(path).open("rb") as handle:
        raw = handle.read(maximum_bytes + 1)
    if len(raw) > maximum_bytes:
        raise ValueError("historical input exceeds configured size limit")
    records = tuple(parse_snapshot_json(line) for line in raw.decode("utf-8").splitlines() if line.strip())
    if not records:
        raise DataUnavailableError("DATA_UNAVAILABLE: empty historical input")
    return records


class LocalHistoricalProvider:
    def __init__(self, records: tuple[HistoricalSnapshot, ...], freshness: FreshnessConfig | None = None,
                 limits: HistoricalDataConfig | None = None):
        self.freshness = freshness or FreshnessConfig()
        # Defensive roundtrip rejects unvalidated copies and detaches input models.
        self._records = tuple(parse_snapshot_json(canonical_snapshot_json(r)) for r in records)
        validate_sequence(self._records, self.freshness, limits)
        groups = {}
        for record in self._records:
            key = (record.chain.underlying, record.chain.expiration, record.session_date)
            groups.setdefault(key, []).append(record)
        self._index = {key: (tuple(r.chain.timestamp for r in values), tuple(values))
                       for key, values in groups.items()}

    def snapshot_at(self, underlying: str, expiration: date, as_of: datetime,
                    *, require_complete: bool = True) -> HistoricalSnapshot:
        as_of = as_utc(as_of)
        key = (underlying, expiration, as_of.astimezone(ZoneInfo(EXCHANGE_TIMEZONE)).date())
        timestamps, values = self._index.get(key, ((), ()))
        position = bisect_right(timestamps, as_of) - 1
        while position >= 0 and values[position].available_at > as_of:
            position -= 1
        if position < 0:
            raise DataUnavailableError("DATA_UNAVAILABLE: no visible historical snapshot")
        latest = values[position]
        validate_snapshot(latest, as_of, self.freshness, require_complete=require_complete)
        return latest
