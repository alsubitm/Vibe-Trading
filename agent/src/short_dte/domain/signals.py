"""Research records; claimed payoff values are not risk-engine approvals."""
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, JsonValue, model_validator

from .base import DomainModel, NonnegativeMoney, Quantity, Text, UTCTimestamp
from .options import OptionContract

Score = Annotated[float, Field(ge=0, le=100)]
Probability = Annotated[float, Field(ge=0, le=1)]


class OptionLeg(DomainModel):
    contract: OptionContract
    side: Literal["BUY", "SELL"]
    quantity: Quantity
    expected_price: NonnegativeMoney
    delta: float
    gamma: float
    theta: float
    vega: float


class ExitPlan(DomainModel):
    time_stop: UTCTimestamp
    profit_target: NonnegativeMoney
    maximum_loss: NonnegativeMoney
    structural_stop: str | None = None


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
        if self.exit_plan.time_stop <= self.timestamp:
            raise ValueError("time stop must follow candidate timestamp")
        return self
