from typing import Annotated, Literal

from pydantic import Field, StrictBool, model_validator

from ..domain.base import DomainModel, Quantity


class FreshnessConfig(DomainModel):
    underlying_quote_seconds: Quantity = 3
    option_quote_seconds: Quantity = 5
    chain_snapshot_seconds: Quantity = 15
    economic_calendar_minutes: Quantity = 60


class BucketConfig(DomainModel):
    enabled: StrictBool = False
    minimum_score: Annotated[float, Field(ge=0, le=100)]


class DTEConfig(DomainModel):
    zero_dte: BucketConfig = BucketConfig(minimum_score=75)
    one_dte: BucketConfig = BucketConfig(minimum_score=72)
    two_three_dte: BucketConfig = BucketConfig(minimum_score=70)
    four_seven_dte: BucketConfig = BucketConfig(minimum_score=68)


class ShortDTEConfig(DomainModel):
    schema_version: Literal[1]
    mode: Literal["research", "replay", "paper"] = "research"
    live_trading_enabled: Literal[False] = False
    universe: tuple[Literal["SPX", "SPY", "QQQ"], ...] = ("SPX", "SPY", "QQQ")
    exchange_timezone: Literal["America/New_York"] = "America/New_York"
    freshness: FreshnessConfig = FreshnessConfig()
    buckets: DTEConfig = DTEConfig()

    @model_validator(mode="after")
    def validate_universe(self):
        if not self.universe or len(self.universe) != len(set(self.universe)):
            raise ValueError("universe must be nonempty and unique")
        return self
