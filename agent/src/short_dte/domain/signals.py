"""Research records; claimed payoff values are not risk-engine approvals."""
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, JsonValue, model_validator

from .base import DomainModel, NonnegativeMoney, PositiveMoney, Quantity, Text, UTCTimestamp
from .options import OptionContract

Score = Annotated[float, Field(ge=0, le=100)]
Probability = Annotated[float, Field(ge=0, le=1)]


class OptionLeg(DomainModel):
    contract: OptionContract
    side: Literal["BUY", "SELL"]
    quantity: Quantity
    expected_price: NonnegativeMoney
    delta: Annotated[float, Field(ge=-1, le=1)]
    gamma: Annotated[float, Field(ge=0)]
    theta: float
    vega: Annotated[float, Field(ge=0)]


class ExitPlan(DomainModel):
    profit_target_pct_max_profit: Annotated[float, Field(gt=0, le=100)] | None = None
    stop_loss_pct_max_loss: Annotated[float, Field(gt=0, le=100)] | None = None
    underlying_invalidation_level: PositiveMoney | None = None
    time_stop: UTCTimestamp | None = None
    forced_exit: UTCTimestamp
    trailing_logic: dict[str, JsonValue] | None = None

    @model_validator(mode="after")
    def validate_deadlines(self):
        if self.time_stop is not None and self.time_stop > self.forced_exit:
            raise ValueError("time stop cannot follow forced exit")
        return self


class TradeCandidate(DomainModel):
    candidate_id: UUID
    timestamp: UTCTimestamp
    underlying: Text
    strategy_id: Text
    legs: tuple[OptionLeg, ...]
    net_debit_credit: Decimal
    max_profit: NonnegativeMoney | None
    max_loss: NonnegativeMoney
    breakevens: tuple[NonnegativeMoney, ...] = ()
    position_delta: float
    position_gamma: float
    position_theta: float
    position_vega: float
    probability_estimates: dict[str, Probability] = Field(default_factory=dict)
    liquidity_score: Score
    volatility_score: Score
    regime_score: Score
    structure_score: Score
    risk_reward_score: Score
    total_score: Score
    exit_plan: ExitPlan
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_candidate(self):
        if not self.legs or any(leg.contract.underlying != self.underlying for leg in self.legs):
            raise ValueError("candidate requires legs with matching underlying")
        if self.exit_plan.forced_exit <= self.timestamp:
            raise ValueError("forced exit must follow candidate timestamp")
        if self.exit_plan.time_stop is not None and self.exit_plan.time_stop <= self.timestamp:
            raise ValueError("time stop must follow candidate timestamp")
        return self
