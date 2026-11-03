"""Providers must honor both event time and publication time; no network methods."""
from datetime import date, datetime
from typing import Protocol

from .models import HistoricalSnapshot


class HistoricalDataProvider(Protocol):
    def snapshot_at(self, underlying: str, expiration: date, as_of: datetime,
                    *, require_complete: bool = True) -> HistoricalSnapshot:
        """Return a fresh, synchronized snapshot or raise a data-integrity error."""
        ...
