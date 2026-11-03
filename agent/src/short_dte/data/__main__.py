"""Explicit research-only local importer. Never registered as an agent/broker tool."""
import argparse
import json
import sys
from pathlib import Path

from src.tools.redaction import redact_payload

from ..config.loader import load_config
from .storage import HistoricalStore, import_jsonl


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Import a local canonical Short DTE JSONL archive")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--config", type=Path)
    args = parser.parse_args(argv)
    try:
        store = HistoricalStore(args.output, config=load_config(args.config))
        paths = import_jsonl(args.input, store)
        print(json.dumps(redact_payload({"status": "IMPORTED", "partition_batches": len(paths),
                                         "real_session_acceptance": "NOT_EVALUATED"}), sort_keys=True))
    except (ValueError, OSError) as exc:
        print(json.dumps(redact_payload({"status": "NO_NEW_TRADES", "error": str(exc)})), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
