"""Synthetic fixtures only: no market-data or broker calls, no real-session claim."""
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.short_dte.data.normalizer import canonical_snapshot_json, normalize_snapshot, parse_snapshot_json
from src.short_dte.data.clock import MarketSession
from src.short_dte.data.coverage import assess_session
from src.short_dte.data.__main__ import main
from src.short_dte.data.providers.local import LocalHistoricalProvider, read_jsonl
from src.short_dte.data.storage import HistoricalStore, import_jsonl
from src.short_dte.data.validation import DataIntegrityError, DataUnavailableError
from src.short_dte.config.models import HistoricalDataConfig

NOW = datetime(2026, 11, 2, 15, tzinfo=timezone.utc)


def raw_snapshot(timestamp=NOW, symbol="SPY", **overrides):
    stamp = timestamp.isoformat()
    contract = dict(underlying=symbol, option_symbol=f"{symbol}-synthetic-C", expiration=timestamp.date().isoformat(),
                    strike="600", right="CALL", multiplier=100,
                    settlement_type="CASH" if symbol == "SPX" else "PHYSICAL",
                    exercise_style="EUROPEAN" if symbol == "SPX" else "AMERICAN")
    call = dict(contract=contract, timestamp=stamp, bid="1.000000001", ask="1.10",
                underlying_price="600", source="synthetic-fixture")
    put = dict(call, contract=dict(contract, option_symbol=f"{symbol}-synthetic-P", right="PUT"))
    data = dict(schema_version=1, dataset_id="SYNTHETIC-UNIT-TEST", data_kind="SYNTHETIC",
                available_at=stamp,
                underlying_quote=dict(symbol=symbol, timestamp=stamp, bid="600", ask="600.01", source="synthetic-fixture"),
                chain=dict(underlying=symbol, timestamp=stamp, underlying_price="600", expiration=timestamp.date().isoformat(),
                           contracts=[call, put], source="synthetic-fixture", is_complete=True, snapshot_kind="COMPLETE"),
                bars=[dict(available_at=stamp, bar=dict(symbol=symbol, timeframe="1m",
                           timestamp=(timestamp - timedelta(minutes=1)).isoformat(),
                           open="600", high="601", low="599", close="600", volume=100, source="synthetic-fixture"))])
    return data | overrides


def snapshot(timestamp=NOW, symbol="SPY", **overrides):
    return normalize_snapshot(raw_snapshot(timestamp, symbol, **overrides))


def write_input(path, records):
    path.write_text("\n".join(canonical_snapshot_json(r) for r in records) + "\n", encoding="utf-8")


def test_canonical_normalization_preserves_optional_fields_and_exact_money():
    value = snapshot()
    assert value.chain.contracts[0].volume is None
    assert str(value.chain.contracts[0].bid) == "1.000000001"
    assert value == parse_snapshot_json(canonical_snapshot_json(value))
    assert value.session_date == date(2026, 11, 2)


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(available_at="2026-11-02T14:59:59Z"),
    lambda d: d.update(schema_version=True),
    lambda d: d.update(unexpected=True),
    lambda d: d["underlying_quote"].update(symbol="QQQ"),
    lambda d: d["underlying_quote"].update(timestamp="2026-11-02T15:00:01Z"),
    lambda d: d["underlying_quote"].update(timestamp="2026-11-02T15:00:00"),
    lambda d: d["chain"].update(expiration="2026-11-01", contracts=[]),
    lambda d: d["chain"].update(contracts=[]),
    lambda d: d["chain"]["contracts"][0].update(ask="0.5"),
    lambda d: d["chain"]["contracts"][0].update(gamma=-1),
    lambda d: d["chain"]["contracts"][0].update(implied_volatility=float("inf")),
    lambda d: d["bars"][0].update(available_at="2026-11-02T14:59:59Z"),
    lambda d: d["bars"][0]["bar"].update(timestamp="2026-11-02T15:00:00Z"),
    lambda d: d["bars"].append(d["bars"][0]),
])
def test_corrupt_records_fail_closed(mutation):
    data = raw_snapshot()
    mutation(data)
    with pytest.raises((ValidationError, DataIntegrityError)):
        normalize_snapshot(data)


def test_open_interest_requires_publication_semantics():
    data = raw_snapshot()
    data["chain"]["contracts"][0]["open_interest"] = 500
    with pytest.raises(ValidationError):
        normalize_snapshot(data)
    data.update(open_interest_as_of="2026-10-30", open_interest_available_at=NOW.isoformat())
    assert normalize_snapshot(data).chain.contracts[0].open_interest == 500
    data["open_interest_available_at"] = (NOW + timedelta(hours=1)).isoformat()
    with pytest.raises(ValidationError):
        normalize_snapshot(data)


def test_no_future_or_unpublished_observations_and_no_stale_fallback():
    records = (snapshot(), snapshot(NOW + timedelta(minutes=1), available_at=NOW + timedelta(minutes=1, seconds=1)))
    provider = LocalHistoricalProvider(records)
    assert provider.snapshot_at("SPY", NOW.date(), NOW) == records[0]
    with pytest.raises(DataUnavailableError):
        provider.snapshot_at("SPY", NOW.date(), NOW - timedelta(microseconds=1))
    with pytest.raises(DataIntegrityError, match="STALE_DATA"):
        provider.snapshot_at("SPY", NOW.date(), NOW + timedelta(seconds=59))
    # The later snapshot's event has happened but has not yet been published.
    with pytest.raises(DataIntegrityError, match="STALE_DATA"):
        provider.snapshot_at("SPY", NOW.date(), NOW + timedelta(minutes=1))
    assert provider.snapshot_at("SPY", NOW.date(), NOW + timedelta(minutes=1, seconds=1)) == records[1]
    with pytest.raises(ValueError):
        provider.snapshot_at("SPY", NOW.date(), NOW.replace(tzinfo=None))


def test_exchange_date_not_utc_date():
    when = datetime(2026, 11, 3, 1, tzinfo=timezone.utc)
    data = raw_snapshot(when)
    data["chain"]["expiration"] = "2026-11-02"
    for quote in data["chain"]["contracts"]:
        quote["contract"]["expiration"] = "2026-11-02"
    record = normalize_snapshot(data)
    assert LocalHistoricalProvider((record,)).snapshot_at("SPY", date(2026, 11, 2), when) == record


@pytest.mark.parametrize("records", [lambda: (snapshot(), snapshot()),
                                      lambda: (snapshot(NOW + timedelta(minutes=1)), snapshot())])
def test_duplicate_or_reverse_input_rejected(records):
    with pytest.raises(DataIntegrityError):
        LocalHistoricalProvider(records())


def test_stale_quote_inside_fresh_chain_rejected():
    data = raw_snapshot()
    data["chain"]["contracts"][0]["timestamp"] = (NOW - timedelta(seconds=6)).isoformat()
    with pytest.raises(DataIntegrityError, match="STALE_DATA"):
        LocalHistoricalProvider((normalize_snapshot(data),))


def test_latest_partial_chain_blocks_instead_of_falling_back():
    partial = raw_snapshot(NOW + timedelta(seconds=1))
    partial["chain"].update(is_complete=False, snapshot_kind="PARTIAL")
    provider = LocalHistoricalProvider((snapshot(), normalize_snapshot(partial)))
    with pytest.raises(DataIntegrityError, match="INCOMPLETE_CHAIN"):
        provider.snapshot_at("SPY", NOW.date(), NOW + timedelta(seconds=1))
    assert not provider.snapshot_at("SPY", NOW.date(), NOW + timedelta(seconds=1), require_complete=False).chain.is_complete


def test_jsonl_rejects_duplicates_malformed_empty_and_oversized(tmp_path):
    path = tmp_path / "input.jsonl"
    path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    with pytest.raises(DataIntegrityError, match="duplicate JSON key"):
        read_jsonl(path)
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(ValueError):
        read_jsonl(path)
    path.write_text("\n", encoding="utf-8")
    with pytest.raises(DataUnavailableError):
        read_jsonl(path)
    write_input(path, (snapshot(),))
    with pytest.raises(ValueError, match="size limit"):
        read_jsonl(path, maximum_bytes=10)


def test_parquet_roundtrip_and_flat_projection(tmp_path):
    import duckdb

    store = HistoricalStore(tmp_path / "archive'quoted")
    records = (snapshot(), snapshot(NOW + timedelta(minutes=1)))
    directory = store.write_batch(records)
    assert directory.parts[-4:-1] == ("SPY", "2026-11-02", "2026-11-02")
    assert store.read_records() == records
    assert store.provider().snapshot_at("SPY", NOW.date(), NOW) == records[0]
    with duckdb.connect(":memory:") as con:
        rows = con.execute("SELECT bid, volume, source FROM read_parquet(?) ORDER BY snapshot_index",
                           [str(directory / "option_quotes.parquet")]).fetchall()
    assert rows[0] == ("1.000000001", None, "synthetic-fixture")
    assert len(rows) == 4
    assert store.write_batch(records) == directory
    assert len(store.read_records()) == 2


def test_batch_overlap_and_partition_mismatch_rejected(tmp_path):
    store = HistoricalStore(tmp_path)
    store.write_batch((snapshot(),))
    with pytest.raises(DataIntegrityError, match="duplicate timestamp"):
        store.write_batch((snapshot(), snapshot(NOW + timedelta(seconds=1))))
    with pytest.raises(DataIntegrityError, match="one batch"):
        store.write_batch((snapshot(), snapshot(symbol="QQQ")))
    with pytest.raises(ValueError):
        store.read_records(session_date="../../outside")
    with pytest.raises(ValueError):
        store.read_records(underlying="../SPY")


@pytest.mark.parametrize("filename", ["snapshots.parquet", "option_quotes.parquet", "manifest.json"])
def test_archive_corruption_never_falls_back(tmp_path, filename):
    store = HistoricalStore(tmp_path)
    directory = store.write_batch((snapshot(),))
    (directory / filename).write_bytes(b"corrupted")
    with pytest.raises(DataIntegrityError):
        store.provider()


def test_whole_input_validated_before_publication(tmp_path):
    store = HistoricalStore(tmp_path / "store")
    path = tmp_path / "input.jsonl"
    path.write_text(canonical_snapshot_json(snapshot()) + "\n{}\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        import_jsonl(path, store)
    assert not store.root.exists()


def test_synthetic_three_symbol_import_replays_deterministically(tmp_path):
    records = tuple(snapshot(NOW + timedelta(minutes=i), symbol)
                    for i in range(3) for symbol in ("SPX", "SPY", "QQQ"))
    path = tmp_path / "synthetic.jsonl"
    write_input(path, records)
    store = HistoricalStore(tmp_path / "store")
    assert len(import_jsonl(path, store)) == 3
    def replay():
        provider = store.provider()
        return [canonical_snapshot_json(provider.snapshot_at(symbol, NOW.date(), NOW + timedelta(minutes=i)))
                for i in range(3) for symbol in ("SPX", "SPY", "QQQ")]
    assert replay() == replay()
    assert all(r.data_kind == "SYNTHETIC" for r in store.read_records())


def test_session_coverage_does_not_certify_synthetic_data():
    bounds = MarketSession(session_date=NOW.date(), opens_at=NOW - timedelta(minutes=1),
                           closes_at=NOW + timedelta(minutes=1))
    records = (snapshot(), snapshot(NOW + timedelta(minutes=1)))
    report = assess_session(LocalHistoricalProvider(records), "SPY", NOW.date(), bounds)
    assert report.expected_points == report.valid_points == 2
    assert not report.real_session_passed and report.real_points == 0
    report = assess_session(LocalHistoricalProvider(records[:1]), "SPY", NOW.date(), bounds)
    assert report.valid_points == 1 and len(report.missing_or_invalid) == 1


def test_session_coverage_rejects_a_provider_that_leaks_future_data():
    class FutureProvider:
        def snapshot_at(self, *args):
            return snapshot(NOW + timedelta(minutes=1))

    bounds = MarketSession(session_date=NOW.date(), opens_at=NOW - timedelta(minutes=1), closes_at=NOW)
    report = assess_session(FutureProvider(), "SPY", NOW.date(), bounds)
    assert report.valid_points == 0


def test_missing_manifest_is_corruption_not_an_empty_archive(tmp_path):
    store = HistoricalStore(tmp_path)
    directory = store.write_batch((snapshot(),))
    (directory / "manifest.json").unlink()
    with pytest.raises(DataIntegrityError):
        store.read_records()


def test_active_or_stale_writer_lock_blocks_publication(tmp_path):
    store = HistoricalStore(tmp_path)
    lock = tmp_path / ".writer.lock"
    lock.write_text("existing writer", encoding="utf-8")
    with pytest.raises(DataIntegrityError, match="writer lock"):
        store.write_batch((snapshot(),))
    assert lock.read_text(encoding="utf-8") == "existing writer"


def test_archive_config_hash_checked(tmp_path):
    store = HistoricalStore(tmp_path)
    directory = store.write_batch((snapshot(),))
    path = directory / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["config_hash"] = "sha256:" + "0" * 64
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(DataIntegrityError, match="config hash"):
        store.read_records()


def test_import_cli_success_and_invalid_input_are_explicit(tmp_path, capsys):
    path = tmp_path / "synthetic.jsonl"
    write_input(path, (snapshot(),))
    assert main([str(path), str(tmp_path / "store")]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["real_session_acceptance"] == "NOT_EVALUATED"
    path.write_text("{}", encoding="utf-8")
    assert main([str(path), str(tmp_path / "invalid-store")]) == 2
    assert json.loads(capsys.readouterr().err)["status"] == "NO_NEW_TRADES"


@pytest.mark.parametrize("mutation", [
    lambda d: d["chain"].update(contracts=d["chain"]["contracts"][:1]),
    lambda d: d["chain"]["contracts"][0].update(delta=-0.5),
    lambda d: d["chain"]["contracts"][1].update(delta=0.5),
    lambda d: d["bars"][0]["bar"].update(timeframe="1d"),
    lambda d: d.update(open_interest_as_of="2026-11-03"),
])
def test_incomplete_sections_invalid_greeks_and_publication_dates(mutation):
    data = raw_snapshot()
    mutation(data)
    with pytest.raises(ValidationError):
        normalize_snapshot(data)


def test_extreme_iv_is_rejected_with_configurable_integrity_ceiling():
    data = raw_snapshot()
    data["chain"]["contracts"][0]["implied_volatility"] = 11
    record = normalize_snapshot(data)
    with pytest.raises(DataIntegrityError, match="IV_ANOMALY"):
        LocalHistoricalProvider((record,))
    assert LocalHistoricalProvider((record,), limits=HistoricalDataConfig(maximum_implied_volatility=12))


def test_failed_publication_is_quarantined_and_unlocks_writer(tmp_path, monkeypatch):
    from src.short_dte.data import storage

    def broken_connection():
        raise OSError("simulated disk failure")

    monkeypatch.setattr(storage, "_connection", broken_connection)
    store = HistoricalStore(tmp_path)
    with pytest.raises(OSError):
        store.write_batch((snapshot(),))
    assert not (tmp_path / ".writer.lock").exists()
    assert store.read_records() == ()
    assert list(store.root.glob("*/*/*/.pending-*"))


@pytest.mark.parametrize("mutation", [
    lambda m: m.update(schema_version=2),
    lambda m: m.update(underlying="QQQ"),
    lambda m: m.update(files={}),
    lambda m: m.update(snapshot_count=999),
    lambda m: m.update(snapshot_count=True),
    lambda m: m.update(batch_id="0" * 64),
])
def test_invalid_archive_metadata_is_rejected(tmp_path, mutation):
    store = HistoricalStore(tmp_path)
    directory = store.write_batch((snapshot(),))
    path = directory / "manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    mutation(data)
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(DataIntegrityError):
        store.read_records()


def test_input_partition_provenance_validated_before_writing(tmp_path):
    path = tmp_path / "mixed.jsonl"
    write_input(path, (snapshot(), snapshot(NOW + timedelta(minutes=1), dataset_id="different")))
    store = HistoricalStore(tmp_path / "store")
    with pytest.raises(DataIntegrityError, match="provenance"):
        import_jsonl(path, store)
    assert not store.root.exists()


def test_committed_synthetic_example_imports_without_real_acceptance(tmp_path):
    path = Path(__file__).parents[1] / "fixtures" / "short_dte" / "synthetic_import.jsonl"
    store = HistoricalStore(tmp_path)
    assert len(import_jsonl(path, store)) == 3
    records = store.read_records()
    assert {r.chain.underlying for r in records} == {"SPX", "SPY", "QQQ"}
    assert all(r.data_kind == "SYNTHETIC" for r in records)
