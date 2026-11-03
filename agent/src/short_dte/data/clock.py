"""Pure clock using explicitly supplied exchange sessions, never weekday guesses."""
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import model_validator

from ..domain.base import DomainModel, UTCTimestamp, as_utc
from ..domain.enums import DTEBucket, SessionPhase

EXCHANGE_TIMEZONE = "America/New_York"


class MarketSession(DomainModel):
    session_date: date
    opens_at: UTCTimestamp
    closes_at: UTCTimestamp

    @model_validator(mode="after")
    def validate_bounds(self):
        zone = ZoneInfo(EXCHANGE_TIMEZONE)
        if self.opens_at >= self.closes_at:
            raise ValueError("session open must precede close")
        if any(t.astimezone(zone).date() != self.session_date
               for t in (self.opens_at, self.closes_at)):
            raise ValueError("session boundaries must match exchange date")
        return self


class MarketClock(DomainModel):
    timestamp_utc: UTCTimestamp
    session_date: date
    exchange_timezone: Literal["America/New_York"] = EXCHANGE_TIMEZONE
    session_phase: SessionPhase

    @model_validator(mode="after")
    def validate_date(self):
        if self.timestamp_utc.astimezone(ZoneInfo(self.exchange_timezone)).date() != self.session_date:
            raise ValueError("session_date must match exchange-local timestamp")
        return self


def market_clock(timestamp: datetime, session: MarketSession | None = None) -> MarketClock:
    """Missing session means CLOSED. Caller supplies holidays and early closes.

    Half-open phase windows: opening first 30 minutes; morning until 11:30;
    midday until 13:30; afternoon until final hour; power hour until close.
    Premarket begins 04:00 and postmarket ends 20:00 exchange local time.
    These are analytical labels, not contract-specific trading permissions.
    """
    timestamp = as_utc(timestamp)
    local = timestamp.astimezone(ZoneInfo(EXCHANGE_TIMEZONE))
    phase = SessionPhase.CLOSED
    if session is not None:
        if session.session_date != local.date():
            raise ValueError("session does not match exchange date")
        if timestamp < session.opens_at:
            if local.time() >= time(4):
                phase = SessionPhase.PREMARKET
        elif timestamp >= session.closes_at:
            if local.time() < time(20):
                phase = SessionPhase.POSTMARKET
        elif timestamp >= session.closes_at - timedelta(hours=1):
            phase = SessionPhase.POWER_HOUR
        elif timestamp < session.opens_at + timedelta(minutes=30):
            phase = SessionPhase.OPENING
        elif local.time() < time(11, 30):
            phase = SessionPhase.MORNING
        elif local.time() < time(13, 30):
            phase = SessionPhase.MIDDAY
        else:
            phase = SessionPhase.AFTERNOON
    return MarketClock(timestamp_utc=timestamp, session_date=local.date(), session_phase=phase)


def dte_bucket(expiration: date, timestamp: datetime) -> DTEBucket:
    days = (expiration - as_utc(timestamp).astimezone(ZoneInfo(EXCHANGE_TIMEZONE)).date()).days
    if not 0 <= days <= 7:
        raise ValueError("expiration outside 0–7 calendar DTE")
    return (DTEBucket.ZERO_DTE if days == 0 else DTEBucket.ONE_DTE if days == 1
            else DTEBucket.TWO_THREE_DTE if days <= 3 else DTEBucket.FOUR_SEVEN_DTE)


def require_fresh(timestamp: datetime, now: datetime, maximum_age_seconds: float) -> None:
    """Reject stale and future data with an injected replay/live reference time."""
    import math

    if not math.isfinite(maximum_age_seconds) or maximum_age_seconds <= 0:
        raise ValueError("maximum age must be finite and positive")
    age = (as_utc(now) - as_utc(timestamp)).total_seconds()
    if age < 0 or age > maximum_age_seconds:
        raise ValueError("STALE_DATA" if age >= 0 else "FUTURE_DATA")
