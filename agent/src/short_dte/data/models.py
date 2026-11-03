"""Historical observations preserve event time AND when information was available."""
from datetime import date, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator, model_validator

from ..domain.base import DomainModel, Text, UTCTimestamp
from ..domain.market import UnderlyingBar, UnderlyingQuote
from ..domain.options import OptionChainSnapshot
from .clock import EXCHANGE_TIMEZONE, dte_bucket


class PublishedBar(DomainModel):
    bar: UnderlyingBar
    available_at: UTCTimestamp

    @model_validator(mode="after")
    def completed_bar(self):
        minutes = {"1m": 1, "5m": 5, "15m": 15, "1h": 60}
        if self.bar.timeframe not in minutes:
            raise ValueError("historical intraday bars require an intraday timeframe")
        # Canonical bar timestamp denotes interval START; OHLC is known at END.
        end = self.bar.timestamp + timedelta(minutes=minutes[self.bar.timeframe])
        if self.available_at < end:
            raise ValueError("bar cannot be available before interval close")
        return self


class HistoricalSnapshot(DomainModel):
    schema_version: Literal[1]
    dataset_id: Text
    data_kind: Literal["REAL", "SYNTHETIC"]
    available_at: UTCTimestamp
    underlying_quote: UnderlyingQuote
    chain: OptionChainSnapshot
    bars: tuple[PublishedBar, ...] = Field(min_length=1)
    open_interest_as_of: date | None = None
    open_interest_available_at: UTCTimestamp | None = None

    @field_validator("schema_version", mode="before")
    @classmethod
    def version_is_integer(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("unsupported historical schema version")
        return value

    @property
    def session_date(self) -> date:
        return self.chain.timestamp.astimezone(ZoneInfo(EXCHANGE_TIMEZONE)).date()

    @model_validator(mode="after")
    def consistent_observations(self):
        chain = self.chain
        dte_bucket(chain.expiration, chain.timestamp)
        if chain.underlying not in {"SPX", "SPY", "QQQ"}:
            raise ValueError("unsupported historical underlying")
        if not chain.contracts:
            raise ValueError("DATA_UNAVAILABLE: empty historical chain")
        if chain.is_complete and {q.contract.right for q in chain.contracts} != {"CALL", "PUT"}:
            raise ValueError("complete historical chain must contain call and put sections")
        for quote in chain.contracts:
            if quote.delta is not None and ((quote.contract.right == "CALL" and quote.delta < 0)
                                            or (quote.contract.right == "PUT" and quote.delta > 0)):
                raise ValueError("provider delta sign disagrees with option right")
        if self.available_at < chain.timestamp:
            raise ValueError("availability precedes snapshot event time")
        if self.underlying_quote.symbol != chain.underlying:
            raise ValueError("underlying/chain symbol mismatch")
        if self.underlying_quote.timestamp > chain.timestamp:
            raise ValueError("FUTURE_DATA: underlying quote")
        seen = set()
        last = {}
        for published in self.bars:
            bar = published.bar
            key = (bar.timeframe, bar.timestamp)
            if bar.symbol != chain.underlying or published.available_at > chain.timestamp:
                raise ValueError("bar mismatch or future/unpublished bar")
            if key in seen or (bar.timeframe in last and bar.timestamp <= last[bar.timeframe]):
                raise ValueError("duplicate or non-monotonic bars")
            seen.add(key)
            last[bar.timeframe] = bar.timestamp
        has_oi = any(q.open_interest is not None for q in chain.contracts)
        if has_oi and (self.open_interest_as_of is None or self.open_interest_available_at is None):
            raise ValueError("open interest requires explicit as-of date and publication time")
        if self.open_interest_as_of is not None and self.open_interest_as_of > self.session_date:
            raise ValueError("future open-interest date")
        if self.open_interest_available_at is not None and self.open_interest_available_at > self.available_at:
            raise ValueError("snapshot exposes unpublished open interest")
        return self
