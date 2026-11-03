from decimal import Decimal
from uuid import UUID
from typing import Literal

from .base import DomainModel, NonnegativeMoney, PositiveMoney, Quantity, Text, UTCTimestamp
from .options import OptionContract


class OptionPosition(DomainModel):
    position_id: UUID
    contract: OptionContract
    side: Literal["LONG", "SHORT"]
    quantity: Quantity
    average_price: NonnegativeMoney


class PortfolioState(DomainModel):
    timestamp: UTCTimestamp
    account_ref: Text
    equity: PositiveMoney
    cash: Decimal
    positions: tuple[OptionPosition, ...] = ()
    realized_pnl: Decimal = Decimal(0)
    unrealized_pnl: Decimal = Decimal(0)
