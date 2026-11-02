"""JSON records using application-owned standard logging handlers."""
import json
import logging
from typing import Any

from src.tools.redaction import redact_payload

from ..domain.events import AuditEvent


def event_payload(event: AuditEvent, details: dict[str, Any] | None = None) -> dict:
    payload = event.model_dump(mode="json")
    payload["details"] = details or {}
    return redact_payload(payload)


def log_event(logger: logging.Logger, event: AuditEvent,
              details: dict[str, Any] | None = None, *, level: int = logging.INFO) -> None:
    """No global handler or root-logger changes; durable audit is a separate sink."""
    logger.log(level, "%s", json.dumps(event_payload(event, details), sort_keys=True,
                                      separators=(",", ":"), allow_nan=False))
