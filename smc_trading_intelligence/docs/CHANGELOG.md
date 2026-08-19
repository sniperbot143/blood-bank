# Changelog

Format: newest first. One entry per phase or fix.

## [0.7.0] — 2026-08-19 — Phase 6: liquidity sweeps

### Added
- `liquidity/sweeps.py` — `detect_sweeps()`: penetration of an INTACT pool
  beyond `min_penetration_atr`, bounded by `max_penetration_atr` (deeper is a
  BREAKOUT, counted separately), with a rejection close back on the origin side
  within `confirm_bars`. Measures magnitude/ATR, rejection/ATR, close location,
  bars-to-reject, pool strength and touches, volume ratio, distance from
  structure and session — the feature block Phase 12 consumes.
- `structure/breaks.py` — `mss_require_swept_origin` is now enforceable: pass a
  `SweepSeries` and a bearish MSS requires a recent BUY_SIDE sweep (mirror for
  bullish). Without sweeps supplied the check is skipped, not silently failed.
- `config/smc_rules.py` — `SweepConfig`; `main.py sweeps`; 12 new tests.

### Verified
- 244/244 tests pass. A sweep is never known before its rejection bar
  (per-bar truncation test).
- 18,539 bars → 175 sweeps from 2,414 pools (7.2%), split 91 buy-side / 84
  sell-side, in 0.77 s.

### Noted
- Sweep rate 7.2% against a 95% eventual-consumption rate is the first
  genuinely selective signal in the system.

## [0.6.0] — 2026-08-19 — Phase 5: liquidity pools & sessions

### Added
- `liquidity/sessions.py` — session assignment in named timezones (DST-correct),
  per-instance high/low/range/open/close, midnight-wrapping windows kept as one
  instance, and a completeness rule so a forming session is never liquidity.
- `liquidity/equal_levels.py` — ATR-tolerance clustering of same-kind swings with
  growth-aware accessors (`price_at`, `member_count_at`, `tightness_at`).
- `liquidity/levels.py` — ten pool kinds (swing, EQH/EQL, PDH/PDL, PWH/PWL,
  session high/low) with side assignment, an INTACT/SWEPT/CONSUMED lifecycle,
  touch counting, a configurable strength model, and `LiquidityMap` queries
  (`known_at`, `intact_at`, `above`, `below`, `nearest_above/below`).
- `config/smc_rules.py` — `SessionWindow`, `SessionConfig`, `LiquidityConfig`.
- `main.py liquidity` — pool inventory, status counts, and the nearest pools
  above and below price with distance, touches and strength.
- 39 new tests (11 sessions + 23 liquidity + 5 liquidity oracle).

### Decisions locked
- Buy-side liquidity is ABOVE price, sell-side BELOW — so a swing high is a
  BUY_SIDE pool.
- A wick beyond a level on the same bar that closes through is CONSUMED, not
  SWEPT. Sweeps require the close to come back.
- Calendar and session pools are confirmed only once their period completes.
- Equal-level clusters grow forward and never rewrite earlier state.
- The strength model is an explicit heuristic with config weights, flagged for
  empirical replacement rather than presented as fact.

### Verified
- 232/232 tests pass.
- Liquidity oracle across 2 parameter sets: a fresh run over `frame[:t+1]`
  reproduces every known pool's price, status, touches and member count.
- Status transitions are monotone; cluster member counts are monotone.
- DST: a Europe/London 08:00 window starts 08:00 UTC on 2024-03-29 and 07:00 UTC
  on 2024-04-01.
- 18,539 bars → 2,414 pools in 0.31 s.

### Noted
- 95% of pools end CONSUMED over this history. The base rate of "level
  eventually broken" is very high, which Phase 6 must account for before
  claiming a sweep means anything.

## [0.5.0] — 2026-08-18 — Phase 4: BOS / CHOCH / MSS

### Added
- `structure/displacement.py` — displacement scoring v1: body/ATR, range/ATR and
  close location, ATR-normalised and direction-aware, with NONE/WEAK/MODERATE/
  STRONG classes. The imbalance component (Phase 8) is wired at weight 0.0.
- `structure/breaks.py` — `detect_breaks()`: BOS (continuation), CHOCH (warning,
  bias → RANGE) and MSS (CHOCH + displacement, bias flips) as three distinct
  event types, plus a pending-CHOCH window, level consumption, a gap-bar guard
  and a break-confirmed bias timeline.
- `structure/market_structure.py` — `iter_levels()` (O(n) streaming levels),
  `attach_breaks()`, `swing_sequence_bias_at()`, `build_structure(with_breaks=True)`.
- `config/smc_rules.py` — `BOSMode`, `DisplacementConfig`, `BreakConfig`.
- `main.py breaks` — events, counts, expired CHOCHs, bias share, `--as-of`.
- 52 new tests (22 displacement + 25 breaks + 5 break oracle).

### Fixed
- A bar could emit CHOCH, MSS *and* a redundant BOS on the same already-broken
  level. Precedence is now MSS > CHOCH > BOS, and a level is consumed when broken.

### Decisions locked
- `bias_source` switches to `BOS_CONFIRMED` when a BreakSeries is attached;
  the Phase 3 reading stays reachable via `swing_sequence_bias_at()`.
- Displacement weights are config, so enabling the Phase 8 component changes
  `rules_hash` rather than silently redefining STRONG.
- `mss_require_swept_origin` exists but defaults to false until Phase 6 can
  supply the real test — the requirement is deferred in the open, not dropped.
- bos.py/choch.py/mss.py merged into `structure/breaks.py`: they share one
  forward pass and one bias state machine (documented in PHASE_4_PLAN §11).

### Verified
- 193/193 tests pass.
- Break oracle across 3 parameter sets: for every bar `t`, a fresh run over
  `frame[:t+1]` reproduces exactly the events known at `t`, with the same bias.
- Displacement scoring of a bar is identical from a truncated and a full frame.
- 18,539 bars → 742 events (120 MSS, 60 expired CHOCH) in 0.22 s.

### Noted
- Break-confirmed bias spends 10.2% of bars in RANGE versus 41.8% for the
  Phase 3 label rule on the same data — the intended stickiness, now measured.

## [0.4.0] — 2026-08-18 — Phase 3: market structure

### Added
- `config/smc_rules.py` — `StructureConfig` (equal-level tolerance, narrow-range
  veto, internal-structure settings) and `SMCRules.internal_swing_config()`.
- `structure/market_structure.py` — `build_structure()` / `analyze_structure()`:
  HH/HL/LH/LL/EQH/EQL labelling, `structural_high/low`, `protected_high/low`,
  a per-bar bias timeline with `BiasChange` transitions, `state_at(t)` time
  travel, `bias_share()`, and optional internal structure on a finer swing setting.
- `main.py structure` — labels, levels, bias share, recent transitions, `--as-of`.
- 28 new tests (23 structure + 5 structure oracle tests).

### Decisions locked
- Bias is **non-sticky** in Phase 3: a broken sequence reads RANGE rather than a
  stale trend. `bias_source` records the derivation (`SWING_SEQUENCE` today,
  `BOS_CONFIRMED` from Phase 4) so the field survives the upgrade.
- A dealing range narrower than `range_atr_mult × ATR` (default 2.0) is RANGE
  whatever the labels say.
- `protected_low` is the last low formed before the current structural high (and
  mirror) — refined to "before the last displacement leg" once Phase 7 lands.
- A swing that supersedes another is labelled against the swing it replaced.
- Two independent bias code paths are kept (fast forward pass and from-scratch
  `state_at`) specifically so they can be cross-checked against each other.

### Verified
- 141/141 tests pass.
- Structure oracle across 3 parameter sets: for every bar `t`, a fresh run over
  `frame[:t+1]` reproduces the full run's bias, structural levels, protected
  levels and labels exactly.
- The two bias paths agree on every bar of 300.
- 18,539 bars → 1,590 labels, 855 bias changes, in 0.13 s.

### Noted
- Bias-change frequency is highly sensitive to the swing setting (1,671 changes
  at left/right=3, 356 at left/right=8). Recorded as a parameter-sensitivity
  risk (KNOWN_ISSUES #3), to be reported as a surface in Phase 19.

## [0.3.0] — 2026-08-18 — Phase 2: swing detection

### Added
- `common/indicators.py` — `true_range`, `wilder_atr` (SMA seed then Wilder
  recursion, NaN until seeded, never back-filled), `rolling_median_causal`.
  Everything in `common/` must be strictly causal and is tested as such.
- `config/smc_rules.py` — `SwingConfig` (mode, left/right, `min_swing_atr`,
  adaptive scaling, `reject_across_gaps`) and `SMCRules` with a `rules_hash`
  fingerprint to be embedded in future signals.
- `structure/swings.py` — `detect_swings()` in three modes (FRACTAL,
  FIXED_LOOKBACK, ATR_ADAPTIVE). `SwingPoint` carries `formed_at_index` /
  `confirmed_at_index` / `superseded_at_index`; `SwingSeries.as_of(t)` returns
  the chain as known at bar `t`. Rejected candidates are retained with reasons
  (`ATR_FILTER`, `NO_ATR`, `NOT_EXTREME`, `SPANS_GAP`).
- `main.py swings` — inspect the chain, tune thresholds, and time-travel with
  `--as-of`.
- 39 new tests, including `tests/test_no_lookahead.py` (the oracle test).

### Decisions locked
- Supersession never deletes: a replaced swing records the bar at which it was
  replaced, so historical state is reproducible rather than rewritten.
- A plateau of equal highs resolves to its FIRST bar (strict left, non-strict
  right); the later equal prints become equal-high liquidity in Phase 5.
- Candidates before ATR is seeded are rejected, not accepted unfiltered.
- Swings spanning a data gap are flagged, not discarded — this narrows the
  blanket gap rule in ARCHITECTURE.md §4, which still holds from Phase 4 on.
- Detectors may now import `common/` in addition to `config/` + numpy/pandas.

### Verified
- 113/113 tests pass.
- Oracle test across 5 parameter sets: for every bar `t`, a fresh run over
  `frame[:t+1]` equals the full run's `as_of(t)`. No repainting.
- Live-replay test: no earlier bar's recorded state is ever rewritten.
- 18,539 bars → 3,168 swings in 0.07 s (FRACTAL) / 0.14 s (ATR_ADAPTIVE).

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
