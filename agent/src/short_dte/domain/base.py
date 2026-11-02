"""Shared immutable validation and UTC normalization."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


UTCTimestamp = Annotated[AwareDatetime, AfterValidator(as_utc)]
PositiveMoney = Annotated[Decimal, Field(gt=0, allow_inf_nan=False)]
NonnegativeMoney = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
Count = Annotated[int, Field(ge=0, strict=True)]
Quantity = Annotated[int, Field(gt=0, strict=True)]
Text = Annotated[str, Field(min_length=1, pattern=r"\S")]


class DomainModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
