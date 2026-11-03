"""Explicit canonical archive boundary: no vendor inference or value fabrication."""
import json
from typing import Any

from .models import HistoricalSnapshot
from .validation import DataIntegrityError


def unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DataIntegrityError("duplicate JSON key")
        result[key] = value
    return result


def normalize_snapshot(record: dict[str, Any]) -> HistoricalSnapshot:
    # Re-validate serialized models too; model_copy/model_construct are not
    # trusted external-provider validation paths.
    return HistoricalSnapshot.model_validate(record)


def parse_snapshot_json(raw: str) -> HistoricalSnapshot:
    data = json.loads(raw, object_pairs_hook=unique_json_object)
    return normalize_snapshot(data)


def canonical_snapshot_json(record: HistoricalSnapshot) -> str:
    return json.dumps(record.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), allow_nan=False)
