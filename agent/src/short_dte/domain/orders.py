"""Order vocabulary only; Milestone 0 does not provide submission methods."""
from typing import Literal
from uuid import UUID

from pydantic import Field

from .base import DomainModel, NonnegativeMoney, UTCTimestamp
from .signals import OptionLeg


class OrderRequest(DomainModel):
    order_id: UUID
    candidate_id: UUID
    timestamp: UTCTimestamp
    legs: tuple[OptionLeg, ...] = Field(min_length=1)
    order_type: Literal["LIMIT"] = "LIMIT"
    limit_price: NonnegativeMoney
    price_effect: Literal["DEBIT", "CREDIT"]
    time_in_force: Literal["DAY", "IOC"] = "DAY"
