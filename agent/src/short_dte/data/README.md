# Historical data importer (M1, importer-first checkpoint)

This is a local research-data boundary, not a data subscription, backtest engine,
pricing model or broker integration. No real historical data are bundled.
The master specification's M1 acceptance remains OPEN until at least 20 complete
real sessions for SPX/SPY/QQQ pass synchronized retrieval checks. Synthetic unit
fixtures exercise the implementation; they do not meet that requirement.

## Local use

From the repository root, using an environment with project dependencies:

```powershell
$env:PYTHONPATH = "agent"
python -m src.short_dte.data path/to/canonical.jsonl path/to/research-archive
```

The input is one UTF-8 canonical `HistoricalSnapshot` JSON object per line.
Unknown fields, duplicate JSON keys, malformed lines and mixed partition
provenance fail the import. No vendor aliases, missing prices, timezones,
settlement rules or completeness flags are guessed. Adapt a licensed source to
this schema explicitly. The sample in
`agent/tests/fixtures/short_dte/synthetic_import.jsonl` is labeled SYNTHETIC.
Never pass broker-account exports or credentials as market data.

Success prints IMPORTED and NOT_EVALUATED for real-session acceptance. Failure
returns exit code 2 and NO_NEW_TRADES. This research module is deliberately not
registered with the main CLI, agent tools, scheduler or broker adapters.

## Input contract

`models.py` is the typed authority; exported schemas are available through
`HistoricalSnapshot.model_json_schema()`. Required top-level fields:

| Field | Meaning |
| --- | --- |
| schema_version | Integer 1 |
| dataset_id | Caller-supplied source/dataset revision; not an API key |
| data_kind | Explicit REAL or SYNTHETIC source declaration |
| available_at | UTC-aware time the whole snapshot was usable |
| underlying_quote | Canonical M0 UnderlyingQuote |
| chain | Canonical M0 OptionChainSnapshot, including completeness provenance |
| bars | Nonempty ordered PublishedBar records |
| open_interest_as_of | Required source-as-of date if any OI is present |
| open_interest_available_at | Required source publication timestamp if any OI is present |

Bar timestamps are interval STARTS. `PublishedBar.available_at` cannot precede
the interval end. For example, a 14:30 UTC 1m bar cannot be used at 14:30:30.
All included bars must have been available by the chain event time. Providers
with end-stamped bars must explicitly convert them before import.

Option/underlying timestamps cannot follow the chain timestamp. Freshness is
checked against the historical event time on import and the injected reference
time on retrieval, never against the machine clock. Bounds are inclusive.
Expired contracts and expirations outside 0–7 exchange-local calendar DTE fail.
Complete chains must include call and put sections; that does NOT prove
exhaustive strike coverage. Missing provider fields remain None. Raw option
delta signs must agree with call/put rights. Finite positive IV above
`historical_data.maximum_implied_volatility` is rejected as IV_ANOMALY; default
10.0 means 1000% decimal volatility, an integrity ceiling, not a strategy signal.

OI publication is explicit. Do not timestamp end-of-day OI as if known at the
market open. If publication happens later, the whole record's available_at must
be at least that late. Retrieval before publication cannot expose it; subsequent
freshness checks may still reject the old chain. To retain intraday quotes when
OI provenance is unavailable, omit OI explicitly rather than invent its timing.

## Parquet storage

Layout:

```text
archive/SPX/2026-11-02/2026-11-02/<content-hash>/
    snapshots.parquet
    option_quotes.parquet
    manifest.json
```

One batch belongs to one underlying/session-date/expiration and dataset/kind.
The lossless canonical snapshot JSON is stored once per snapshot in Parquet.
A second Parquet file exposes flat option quote columns from specification §11,
including timestamps, bid/ask, sizes, volume, OI, IV, Greeks and source. Exact
Decimal fields are UTF-8 decimal strings to avoid silent float rounding; UTC
timestamps in the flat projection are Parquet timezone-aware timestamps.

The manifest contains the validated configuration and config hash, source
declaration, row count and SHA-256 file checksums. Batch identity hashes canonical
payloads plus the configuration hash. Read-time validation checks file hashes,
partition identity, config integrity, row sequence and source fields; corruption
raises, never falls back to fabricated data. Checksums detect corruption, not
malicious rewriting of both data and manifest; manifests are not signed.

Writes hold an exclusive local writer lock. Both Parquet files and the manifest
are staged in a hidden directory, then renamed together into an immutable batch.
Publication is atomic per partition on the local filesystem, not a durable,
multi-partition database transaction. An interrupted I/O operation may leave
completed partitions and quarantined `.pending-*` folders. Readers ignore only
pending folders; a missing manifest in a committed batch fails closed. Identical
retries are idempotent; overlapping timestamps in different batches are rejected.
The code does not delete old research data. A stale `.writer.lock` requires an
operator to verify no writer is active before removing it; no automatic stale-lock
takeover or crash recovery is claimed. Use a new archive root when deliberately
reimporting the same history under changed validation settings.

Existing DuckDB performs local Parquet I/O; automatic extension loading and
installation are disabled. No pandas, PyArrow or additional runtime dependency
was introduced. Storage paths stay under the explicit root, including Windows
long-path handling. This version assumes a trusted local filesystem; it does not
claim resilience against a hostile process swapping symlinks during I/O.

## Retrieval and coverage

```python
store = HistoricalStore(root)
provider = store.provider()
snapshot = provider.snapshot_at("SPX", expiration, simulated_timestamp)
```

The provider validates and indexes the archive into memory. It returns the
latest snapshot whose event AND availability times are no later than the
injected clock, within that exchange-local session. A latest stale or partial
visible snapshot fails closed; it does not fall back to an older complete record.
Complete chains are required by default; a research caller may explicitly request
partial records, but that is not authorization to compute full-chain GEX.
This is an in-memory retrieval boundary, not the event-driven replay engine of M7.

`assess_session` checks every minute close from supplied session open+1m through
session close. It requires complete fresh chains and that minute's completed bar.
It reports expected/valid/source-declared-real points and gap timestamps. Synthetic
points never qualify as real-session acceptance. REAL is an input provenance
claim, not independent verification. Callers must supply verified exchange
sessions and confirm source licensing, quote coverage, publication timing and
SPX settlement semantics. No exchange holidays or early closes are guessed.

## Validation

Offline checks cover time visibility, stale/corrupt data, OI publication, duplicate
and reversed observations, exact Decimal round trips, multi-symbol retrieval,
Parquet corruption, atomic-publication failure, writer locking, session gaps,
synthetic-vs-real reporting and CLI failure status. Dedicated CI runs Python
3.11/3.14 on Linux/Windows and enforces a 90% combined data/domain coverage floor.
Its coverage config excludes actual placeholder statements, not implemented
methods whose tuple annotations contain ellipses.

Still required for full M1: a real source adapter/export, 20 complete licensed
SPX/SPY/QQQ sessions, verified session calendars and real-data acceptance evidence.
No M2 analytics or strategy/risk/execution implementation is included.
