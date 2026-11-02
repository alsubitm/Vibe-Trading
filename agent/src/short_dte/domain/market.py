from typing import Literal

from pydantic import model_validator

from .base import Count, DomainModel, PositiveMoney, Text, UTCTimestamp


class UnderlyingQuote(DomainModel):
    symbol: Text
    timestamp: UTCTimestamp
    bid: PositiveMoney
    ask: PositiveMoney
    last: PositiveMoney | None = None
    bid_size: Count | None = None
    ask_size: Count | None = None
    source: Text

    @model_validator(mode="after")
    def validate_spread(self):
        if self.ask < self.bid:
            raise ValueError("ask must be >= bid")
        return self


class UnderlyingBar(DomainModel):
    symbol: Text
    timeframe: Literal["1m", "5m", "15m", "1h", "1d"]
    timestamp: UTCTimestamp
    open: PositiveMoney
    high: PositiveMoney
    low: PositiveMoney
    close: PositiveMoney
    volume: Count
    vwap: PositiveMoney | None = None
    source: Text

    @model_validator(mode="after")
    def validate_ohlc(self):
        if not (self.low <= self.open <= self.high and self.low <= self.close <= self.high):
            raise ValueError("OHLC values must lie within low/high")
        return self
