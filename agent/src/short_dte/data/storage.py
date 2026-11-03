"""Immutable, checksummed local Parquet batches using the existing DuckDB dependency.

The snapshot payload is lossless canonical JSON in Parquet; a flat quote table
provides the specification's queryable minimum columns. Neither is a price model.
Each publication is a single partition, atomically renamed into place only after
both files and the manifest have been written. Existing batches are never edited.
"""
import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from ..config.loader import config_hash, load_config
from ..config.models import ShortDTEConfig
from .models import HistoricalSnapshot
from .normalizer import canonical_snapshot_json, parse_snapshot_json, unique_json_object
from .providers.local import LocalHistoricalProvider, read_jsonl
from .validation import DataIntegrityError, DataUnavailableError, validate_sequence


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _connection():
    import duckdb

    return duckdb.connect(":memory:", config={"autoinstall_known_extensions": False,
                                             "autoload_known_extensions": False})


def _sql_path(path: Path) -> str:
    # COPY needs a SQL string literal; every path is generated beneath root.
    return "'" + str(path).replace("'", "''") + "'"


class HistoricalStore:
    def __init__(self, root: Path, config: ShortDTEConfig | None = None):
        self.root = Path(root).resolve()
        # The packaged desktop Python may not opt into Windows long paths.
        # Partition names + full content hashes must remain readable there.
        if os.name == "nt" and not str(self.root).startswith("\\\\?\\"):
            raw = str(self.root)
            self.root = Path("\\\\?\\UNC\\" + raw[2:] if raw.startswith("\\\\") else "\\\\?\\" + raw)
        self.config = config or load_config()
        self.freshness = self.config.freshness

    def _inside(self, path: Path) -> Path:
        path = path.resolve()
        if not path.is_relative_to(self.root):
            raise DataIntegrityError("archive path escapes explicit storage root")
        return path

    @contextmanager
    def _writer(self):
        self.root.mkdir(parents=True, exist_ok=True)
        lock = self.root / ".writer.lock"
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise DataIntegrityError("archive writer active or stale writer lock; operator review required") from exc
        try:
            yield
        finally:
            os.close(descriptor)
            lock.unlink()

    def write_batch(self, records: tuple[HistoricalSnapshot, ...]) -> Path:
        with self._writer():
            return self._write_batch(records)

    def _write_batch(self, records: tuple[HistoricalSnapshot, ...]) -> Path:
        if not records:
            raise DataUnavailableError("DATA_UNAVAILABLE: empty batch")
        records = tuple(parse_snapshot_json(canonical_snapshot_json(r)) for r in records)
        validate_sequence(records, self.freshness, self.config.historical_data)
        first = records[0]
        partition = (first.chain.underlying, first.session_date, first.chain.expiration)
        if any((r.chain.underlying, r.session_date, r.chain.expiration) != partition for r in records):
            raise DataIntegrityError("one batch must have one underlying/session_date/expiration")
        if any((r.dataset_id, r.data_kind) != (first.dataset_id, first.data_kind) for r in records):
            raise DataIntegrityError("batch provenance must be consistent")
        payloads = [canonical_snapshot_json(r) for r in records]
        batch_id = _digest((config_hash(self.config) + "\n" + "\n".join(payloads)).encode("utf-8"))
        parent = self._inside(self.root / partition[0] / str(partition[1]) / str(partition[2]))
        destination = self._inside(parent / batch_id)
        if destination.exists():
            # An idempotent retry still verifies existing content, never trusts
            # directory names as proof that an earlier write completed safely.
            existing = self._read_batch(destination / "manifest.json")
            if existing != records:
                raise DataIntegrityError("batch identity collision")
            return destination
        # Do not permit ambiguous histories when batches overlap.
        old = self.read_records(underlying=partition[0], session_date=str(partition[1]),
                                expiration=str(partition[2]))
        timestamps = {r.chain.timestamp for r in old}
        if timestamps.intersection(r.chain.timestamp for r in records):
            raise DataIntegrityError("duplicate timestamp across archive batches")
        parent.mkdir(parents=True, exist_ok=True)
        # Hidden staging directories are never discoverable as committed batches.
        stage = Path(tempfile.mkdtemp(prefix=".pending-", dir=parent))
        try:
            with _connection() as con:
                con.execute("CREATE TABLE snapshots (ordinal BIGINT, payload_json VARCHAR)")
                con.executemany("INSERT INTO snapshots VALUES (?, ?)", list(enumerate(payloads)))
                con.execute(f"COPY snapshots TO {_sql_path(stage / 'snapshots.parquet')} (FORMAT PARQUET)")
                con.execute("""CREATE TABLE option_quotes (
                    snapshot_index BIGINT, timestamp TIMESTAMPTZ, available_at TIMESTAMPTZ,
                    underlying VARCHAR, underlying_price VARCHAR, expiration DATE,
                    option_symbol VARCHAR, strike VARCHAR, "right" VARCHAR, multiplier BIGINT,
                    settlement_type VARCHAR, exercise_style VARCHAR, exchange VARCHAR,
                    bid VARCHAR, ask VARCHAR, bid_size BIGINT, ask_size BIGINT, last VARCHAR,
                    volume BIGINT, open_interest BIGINT, iv DOUBLE, delta DOUBLE, gamma DOUBLE,
                    theta DOUBLE, vega DOUBLE, rho DOUBLE, source VARCHAR)
                """)
                rows = []
                for index, record in enumerate(records):
                    for quote in record.chain.contracts:
                        c = quote.contract
                        rows.append((index, quote.timestamp, record.available_at, c.underlying,
                                     str(quote.underlying_price), c.expiration, c.option_symbol,
                                     str(c.strike), c.right, c.multiplier, c.settlement_type,
                                     c.exercise_style, c.exchange, str(quote.bid), str(quote.ask),
                                     quote.bid_size, quote.ask_size,
                                     str(quote.last) if quote.last is not None else None,
                                     quote.volume, quote.open_interest, quote.implied_volatility,
                                     quote.delta, quote.gamma, quote.theta, quote.vega, quote.rho, quote.source))
                con.executemany("INSERT INTO option_quotes VALUES (" + ",".join(["?"] * 27) + ")", rows)
                con.execute(f"COPY option_quotes TO {_sql_path(stage / 'option_quotes.parquet')} (FORMAT PARQUET)")
            files = {name: _digest((stage / name).read_bytes())
                     for name in ("snapshots.parquet", "option_quotes.parquet")}
            manifest = {"schema_version": 1, "batch_id": batch_id,
                        "underlying": partition[0], "session_date": str(partition[1]),
                        "expiration": str(partition[2]), "dataset_id": first.dataset_id,
                        "data_kind": first.data_kind, "snapshot_count": len(records), "files": files,
                        "config": self.config.model_dump(mode="json"), "config_hash": config_hash(self.config)}
            (stage / "manifest.json").write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
            os.rename(stage, destination)
        except (DataIntegrityError, OSError):
            # Leave failed staging data quarantined for diagnosis; never publish
            # or erase existing research data. Readers ignore .pending-* folders.
            raise
        except Exception as exc:
            raise DataIntegrityError("archive publication failed; staged batch quarantined") from exc
        return destination

    def _read_batch(self, manifest_path: Path) -> tuple[HistoricalSnapshot, ...]:
        manifest_path = self._inside(manifest_path)
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"), object_pairs_hook=unique_json_object)
            if manifest_path.relative_to(self.root).parts[:3] != (
                    manifest["underlying"], manifest["session_date"], manifest["expiration"]):
                raise DataIntegrityError("archive path/partition mismatch")
            if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1:
                raise DataIntegrityError("unsupported archive schema")
            if type(manifest["snapshot_count"]) is not int or manifest["snapshot_count"] < 1:
                raise DataIntegrityError("invalid snapshot count")
            stored_config = ShortDTEConfig.model_validate(manifest["config"])
            if config_hash(stored_config) != manifest["config_hash"]:
                raise DataIntegrityError("archive config hash mismatch")
            if set(manifest["files"]) != {"snapshots.parquet", "option_quotes.parquet"}:
                raise DataIntegrityError("invalid archive file set")
            for filename, digest in manifest["files"].items():
                if _digest(self._inside(manifest_path.parent / filename).read_bytes()) != digest:
                    raise DataIntegrityError("archive checksum mismatch")
            with _connection() as con:
                rows = con.execute("SELECT ordinal, payload_json FROM read_parquet(?) ORDER BY ordinal",
                                   [str(manifest_path.parent / "snapshots.parquet")]).fetchall()
            if [r[0] for r in rows] != list(range(manifest["snapshot_count"])):
                raise DataIntegrityError("archive row count/sequence mismatch")
            records = tuple(parse_snapshot_json(row[1]) for row in rows)
            payload = (manifest["config_hash"] + "\n" + "\n".join(canonical_snapshot_json(r) for r in records)).encode("utf-8")
            if _digest(payload) != manifest["batch_id"] or manifest_path.parent.name != manifest["batch_id"]:
                raise DataIntegrityError("archive batch hash mismatch")
            for r in records:
                if (r.chain.underlying, str(r.session_date), str(r.chain.expiration), r.dataset_id, r.data_kind) != (
                        manifest["underlying"], manifest["session_date"], manifest["expiration"],
                        manifest["dataset_id"], manifest["data_kind"]):
                    raise DataIntegrityError("archive partition/provenance mismatch")
            validate_sequence(records, self.freshness, self.config.historical_data)
            return records
        except DataIntegrityError:
            raise
        except Exception as exc:
            raise DataIntegrityError("corrupt historical archive") from exc

    def read_records(self, *, underlying: str | None = None, session_date: str | None = None,
                     expiration: str | None = None) -> tuple[HistoricalSnapshot, ...]:
        if underlying is not None and underlying not in {"SPX", "SPY", "QQQ"}:
            raise ValueError("unsupported underlying")
        # Never turn caller-provided date strings into paths or glob patterns.
        from datetime import date

        for value in (session_date, expiration):
            if value is not None and date.fromisoformat(value).isoformat() != value:
                raise ValueError("partition dates must use ISO format")
        records = []
        for directory in sorted(self.root.glob("*/*/*/*")):
            if not directory.is_dir() or directory.name.startswith("."):
                continue
            path = directory / "manifest.json"
            parts = path.relative_to(self.root).parts
            if underlying is not None and parts[0] != underlying:
                continue
            if session_date is not None and parts[1] != session_date:
                continue
            if expiration is not None and parts[2] != expiration:
                continue
            records.extend(self._read_batch(path))
        result = tuple(sorted(records, key=lambda r: (r.chain.timestamp, r.chain.underlying, r.chain.expiration)))
        validate_sequence(result, self.freshness, self.config.historical_data)
        return result

    def provider(self) -> LocalHistoricalProvider:
        return LocalHistoricalProvider(self.read_records(), self.freshness, self.config.historical_data)


def import_jsonl(path: Path, store: HistoricalStore) -> tuple[Path, ...]:
    """Validate the entire input before writing partition-sized immutable batches.

    Publication is atomic PER PARTITION, not a multi-partition transaction. A
    retry is idempotent for identical partition batches if an I/O error interrupted
    an import; no invalid input is silently skipped.
    """
    records = read_jsonl(path)
    validate_sequence(records, store.freshness, store.config.historical_data)
    groups = {}
    for r in records:
        key = (r.chain.underlying, r.session_date, r.chain.expiration)
        groups.setdefault(key, []).append(r)
    for batch in groups.values():
        if len({(r.dataset_id, r.data_kind) for r in batch}) != 1:
            raise DataIntegrityError("batch provenance must be consistent")
    return tuple(store.write_batch(tuple(batch)) for batch in groups.values())
