# Short DTE foundation (Milestone 0)

Research-only foundation for SPX/SPY/QQQ with 0–7 calendar DTE. Nothing is
registered with a CLI, agent tool, scheduler, broker, or live-order gate.

Imports follow this repository's `src.*` namespace. The existing Pydantic v2,
PyYAML, pytest configuration and test-home sandbox are reused. Audit persistence
uses `src.governance.ledger.append_record`; redaction uses
`src.tools.redaction.redact_payload`. The existing paper-trade calendar does not
provide US sessions, so this package accepts explicit `MarketSession` records
instead of duplicating it or guessing US holidays. No new calendar dependency.

## Contracts

- Monetary values use finite Decimal values. Counts are strict integers.
- Naive timestamps fail validation; aware inputs normalize to UTC. Exchange
  date and calendar DTE use America/New_York, including DST.
- Domain models reject unknown fields. Contracts are frozen; chain members,
  candidate legs and portfolio positions are immutable tuples.
  Candidate metadata/probability mappings remain mutable; callers must not
  treat frozen model attributes as a deep-immutability guarantee.
- Missing provider fields remain None. Zero option bids are representable for
  downstream liquidity rejection. Freshness is an explicit runtime check with
  an injected reference timestamp, never a hidden wall-clock read.
- Chains reject duplicate identities, future quotes, mismatched expirations
  and inconsistent completeness declarations. A complete chain must be nonempty.
  Completeness is provider provenance, not proof of exhaustive strike coverage.
- The clock has no network or system-clock dependency. Missing sessions report
  CLOSED. Supply verified regular/early-close boundaries for the exchange-local
  date. Phase labels are analytical; they do not authorize contract trading or
  determine SPX AM/PM settlement or last-trade times.
- YAML requires explicit schema_version 1. Unknown fields, duplicate keys,
  unsupported versions and live mode fail. Config and nested settings are frozen;
  universe uses a tuple. No environment overrides or credential loading.
  Direct configuration construction also rejects bool/float schema versions
  and numeric substitutes for the disabled live flag.
- The SHA-256 hash covers canonical JSON of the validated, default-expanded
  configuration. YAML key order/comments do not change it; setting changes do.
- Every research audit event has version, UTC timestamp, event/run IDs and config
  hash. The caller supplies IDs/time for replay determinism and an explicit
  dedicated research path. The shared ledger provides locking, sequencing,
  fsync and hash chaining. Open/write failures propagate; the shared ledger
  warns and degrades to flush-only if fsync is unsupported. Do not use the live ledger
  path or include credentials in event labels/details.
  Audit details must be JSON-serializable with finite numbers. Tuple arrays
  normalize to lists before redaction; opaque objects fail before writing.

## Validation

From the repository root: `python -m pytest agent/tests/short_dte --disable-socket -q`.
The standard full-suite CI discovers these tests automatically. The dedicated
Short DTE workflow additionally runs on the feature branch and relevant pull
requests, tests Python 3.11/3.14 on Linux/Windows, and checks packaged defaults.

Milestone 0 acceptance: imports and packaged defaults work; malformed records,
naive times, invalid configs and future/stale data fail; DST/session boundaries,
early close and exchange-date DTE are covered; config hashes are reproducible;
audit redaction/chaining and I/O failures are covered; no live execution wiring.

## Later milestones

Package seams reserve data providers, deterministic features, strategies,
scoring, risk, execution simulation, replay, paper, orchestration and optional
LLM explanations. No placeholder returns imply those engines are implemented.
Candidate payoff and order records are data vocabulary, not proof of bounded
risk or an execution approval. Strategy payoff validation, exchange calendar
adapters, pricing/Greeks engines, risk enforcement and order state transitions
must be implemented and tested before these records can drive trading.
