# Changelog

Format: newest first. One entry per phase or fix.

## [0.2.0] — 2026-08-18 — Phase 1: data layer

### Added
- `config/settings.py` — frozen Pydantic settings, timeframe registry (M1…D1),
  `.env` loading. No credential is required to start.
- `data/normalizer.py` — the schema gate: column aliasing (incl. MT5 `<ANGLE>`
  headers), UTC conversion (naive/epoch shifted by broker offset, tz-aware
  converted), sort, dedup keep-last, OHLC sanity quarantine, gap map with
  weekend/weekday classification, `is_closed` + forming-bar drop,
  `validate_frame()`, `closed_bars()`.
- `data/csv_loader.py` — CSV/TSV/Parquet with separator sniffing, MT5
  `<DATE>`/`<TIME>` recombination, explicit column maps.
- `data/mt5_connector.py` — read-only MT5 wrapper, import-guarded for non-Windows;
  `resolve_symbol` across 17 broker suffixes; chunked fetch so the terminal's
  per-call limit cannot silently truncate history; live server-UTC-offset detection.
- `data/cache.py` — parquet cache + JSON manifest (rows, span, digits, offset,
  content hash, source), incremental merge with gap map recomputed across the join.
- `main.py` — `ingest`, `inspect`, `symbols`, `status`.
- `tools/make_synthetic_csv.py` — MT5-style sample export so the pipeline runs
  without MT5. Test scaffolding, never a probability input.
- `tests/` — 74 tests: schema contract, alias handling, timezone cases, dedup,
  quarantine, gap classification, forming-bar rules, cache round-trip/merge/
  idempotency, MT5 helper purity, read-only assertion, CLI end-to-end.
- `requirements.txt`, `.env.example`, `.gitignore`, `pytest.ini`.

### Verified
- 74/74 tests pass.
- 18,541-row synthetic export → 18,539 cached bars, 1 duplicate removed,
  1 impossible-OHLC row quarantined, 13 gaps (12 weekend, 1 weekday).
- Re-ingest is byte-identical: manifest content hash unchanged.
- MT5-absent path exits with code 2 and an actionable message, not a traceback.

### Not verified here
- The MT5 fetch path itself (Windows-only). Needs a run against your terminal.

## [0.1.0-design] — 2026-08-18

### Added
- Phase 0 design freeze. No application code.
- `docs/ARCHITECTURE.md` — 9-layer architecture, data contract, module map,
  five look-ahead guards, storage schema, runtime modes, MT5 execution path,
  seven named weaknesses.
- `docs/SMC_DEFINITIONS.md` — deterministic definitions for swings, structure,
  BOS, CHOCH, MSS, displacement, liquidity, sweeps, FVG, IFVG, order blocks,
  breakers, premium/discount, sessions, regime, and the v0.1 setup taxonomy.
- `docs/PROBABILITY_METHODOLOGY.md` — setup database, outcome labeling, tiered
  similarity back-off, beta-binomial (Jeffreys) estimator with Wilson and
  block-bootstrap intervals, reliability tiers, calibration, no-look-ahead rules,
  decision engine thresholds and vetoes.
- `docs/PHASE_1_PLAN.md` — proposed first implementation phase.
- `COST_AUDIT.md` — ₹0 core stack; optional and rejected components.
- `README.md`, `docs/KNOWN_ISSUES.md`.

### Decisions locked
- Setup score and probability are separate numbers, never derived from each other.
- Every SMC object carries `formed_at_index` and `confirmed_at_index`; only
  confirmed objects are readable by the setup builder.
- Probability comparables are filtered by `resolved_at < signal_time`.
- Intrabar TP/SL ambiguity resolves to `SL_FIRST` unless M1 data is present.
- MSS requires displacement; CHOCH does not. They are distinct event types.

### Pending approval
- Phase 1 scope (data layer). No code is written until approved.
