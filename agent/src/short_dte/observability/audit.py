"""Reuse shared redaction and tamper-evident ledger primitives."""
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from src.governance.ledger import append_record

from ..config.loader import config_hash
from ..config.models import ShortDTEConfig
from ..domain.events import AuditEvent
from .logging import event_payload


def make_event(*, config: ShortDTEConfig, event_id: UUID, timestamp: datetime,
               run_id: str, event_type: str, **kwargs) -> AuditEvent:
    return AuditEvent(event_id=event_id, timestamp=timestamp, run_id=run_id,
                      event_type=event_type, config_hash=config_hash(config), **kwargs)


def write_audit(path: Path, event: AuditEvent, details: dict[str, Any] | None = None):
    """Caller supplies a dedicated research path; write failures propagate.

    Do not pass broker payloads or credentials. Shared redaction is defense in
    depth, not permission to collect secrets. Hash chaining is supplied by the
    existing governance ledger; no live audit API or default live path is used.
    """
    return append_record(path, event_payload(event, details))
