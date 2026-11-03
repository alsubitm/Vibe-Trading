from typing import Literal
from uuid import UUID
from pydantic import Field

from .base import DomainModel, Text, UTCTimestamp


class AuditEvent(DomainModel):
    schema_version: Literal[1] = 1
    event_id: UUID
    timestamp: UTCTimestamp
    run_id: Text
    event_type: Text
    config_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    decision: Literal["OBSERVE", "NO_NEW_TRADES", "REJECT"] = "OBSERVE"
    reason_codes: tuple[Text, ...] = ()
