from datetime import date
from typing import Annotated, Literal

from pydantic import Field, StrictBool, model_validator

from .base import (
    Count, DomainModel, NonnegativeMoney, PositiveMoney, Quantity, Text, UTCTimestamp,
)


class OptionContract(DomainModel):
    underlying: Text
    option_symbol: Text
    expiration: date
    strike: PositiveMoney
    right: Literal["CALL", "PUT"]
    multiplier: Quantity
    settlement_type: Literal["PHYSICAL", "CASH"]
    exercise_style: Literal["AMERICAN", "EUROPEAN"]
    exchange: Text | None = None


class Greeks(DomainModel):
    delta: Annotated[float, Field(ge=-1, le=1)]
    gamma: Annotated[float, Field(ge=0)]
    theta: float
    vega: Annotated[float, Field(ge=0)]
    rho: float
    vanna: float | None = None
    charm: float | None = None
    vomma: float | None = None


class OptionQuote(DomainModel):
    contract: OptionContract
    timestamp: UTCTimestamp
    bid: NonnegativeMoney
    ask: PositiveMoney
    bid_size: Count | None = None
    ask_size: Count | None = None
    last: NonnegativeMoney | None = None
    volume: Count | None = None
    open_interest: Count | None = None
    implied_volatility: Annotated[float, Field(gt=0)] | None = None
    delta: Annotated[float, Field(ge=-1, le=1)] | None = None
    gamma: Annotated[float, Field(ge=0)] | None = None
    theta: float | None = None
    vega: Annotated[float, Field(ge=0)] | None = None
    rho: float | None = None
    underlying_price: PositiveMoney
    source: Text

    @model_validator(mode="after")
    def validate_spread(self):
        if self.ask < self.bid:
            raise ValueError("ask must be >= bid")
        return self


class OptionChainSnapshot(DomainModel):
    underlying: Text
    timestamp: UTCTimestamp
    underlying_price: PositiveMoney
    expiration: date
    contracts: tuple[OptionQuote, ...]
    source: Text
    is_complete: StrictBool
    snapshot_kind: Literal["COMPLETE", "PARTIAL", "STREAMING_SUBSET"] = "PARTIAL"

    @model_validator(mode="after")
    def validate_contracts(self):
        if self.is_complete != (self.snapshot_kind == "COMPLETE"):
            raise ValueError("completeness must agree with snapshot_kind")
        symbols = set()
        identities = set()
        for quote in self.contracts:
            contract = quote.contract
            identity = (contract.expiration, contract.strike, contract.right, contract.multiplier,
                        contract.settlement_type, contract.exercise_style, contract.exchange)
            if contract.underlying != self.underlying or contract.expiration != self.expiration:
                raise ValueError("chain contract underlying/expiration mismatch")
            if contract.option_symbol in symbols or identity in identities:
                raise ValueError("duplicate contract")
            if quote.timestamp > self.timestamp:
                raise ValueError("chain cannot contain future quotes")
            symbols.add(contract.option_symbol)
            identities.add(identity)
        if self.is_complete and not self.contracts:
            raise ValueError("complete chain cannot be empty")
        return self
