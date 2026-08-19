# Changelog

Format: newest first. One entry per phase or fix.

## [1.1.0] — 2026-08-19 — First real market data

### Added
- `data/csv_loader.py` — headerless CSV support via named `LAYOUTS`
  (`histdata_mt`, `ohlcv`, `ohlc`) and `main.py ingest --format`. A headerless
  file without `--format` is now a clear error naming the layouts, instead of
  silently reading the first price row as column names.
- `data/normalizer.py` — `on_duplicate` policy (`last` / `first` / `widest`),
  exposed as `ingest --on-duplicate`. Default is unchanged (`last`).
- `main.py resample` — build a higher timeframe from cached bars (M1 → M5 →
  M15 → H1), reusing the tested complete-bucket rule so a partial bucket can
  never become a bar.

### Fixed
- `context/mtf_bias.py` — when the completeness mask emptied the result,
  `np.r_[False, <empty index>]` produced a length-1 array that replaced the
  empty DatetimeIndex with a RangeIndex holding one all-NaN phantom bar. The
  early `agg.empty` check ran before the mask and so never caught it. Now
  re-checked after. Found by resampling to a timeframe the source could not
  fill; it would have surfaced as a schema-validation failure far from the cause.

### Verified — the first real data through the system
- Source: HistData.com `DAT_MT_XAUUSD_M1_202607.csv`, XAUUSD M1, July 2026,
  31,414 raw rows. Free, and the closest thing to MT5 history available without
  Windows.
- **Timezone confirmed from the data, not assumed.** HistData MT files are New
  York time; July is EDT, so `--offset -4`. The check: all 18 non-weekend gaps
  land at 20:59 → 22:00 UTC, which is 16:59 → 18:00 New York — exactly the
  daily break HistData's own report describes. A wrong offset moves those gaps.
- **276 duplicate timestamps, and the file's stamps go backwards 138 times.**
  The file interleaves degenerate filler bars (open=high=low=close) with real
  ones. Of 264 duplicated stamps, 118 have the filler first and 111 have it
  second — so position tells you nothing, and the default `last` policy was
  discarding the real bar roughly half the time. `--on-duplicate widest` cuts
  flat bars from 120 to 1.
- 31,138 M1 bars → 6,226 M5, 2,074 M15, 517 H1.
- Census (`database/xauusd_real.db`, kept separate from the synthetic one):
  2,784 candidates, **93 not superseded**, built in 8.3 s.
- DETERMINISTIC backtest, 66 trades: 28.8% win rate, +0.193R expectancy,
  PF 1.27, 12.7R total, max drawdown 13.2R, worst streak 9 losses.
- DECISION backtest: **0 trades.** 59 vetoed LOW_RELIABILITY_VERY_LOW, 7
  INSUFFICIENT_SAMPLE. Walk-forward: 0 out-of-sample trades across all 4 folds.
- Monte Carlo on the 66 deterministic trades: IID median +11.5R, BLOCK median
  +15.1R. The two agree here, unlike on the synthetic data — but on 66 trades
  from one month neither number is evidence of anything.

### Noted
- One month is not a sample. 93 independent setups spread over 4 walk-forward
  folds is why the decision engine takes nothing, and it is the correct answer,
  not a threshold to lower. The deterministic +0.193R is measured on 66
  overlapping trades from a single month of a single instrument.
- Volume is 0 for every bar in the HistData MT format. Volume-derived features
  are absent on this dataset, not zero-valued.

## [1.0.0] — 2026-08-19 — Phases 21-24: viewer, narration, paper and live

### Added
- `tradingview/smc_structure.pine` (Phase 21) — a Pine v6 **viewer**, free tier
  only. `ta.pivothigh/low` delay by `swingRight` bars, and that delay IS the
  confirmation rule, so nothing repaints. Python owns every number; the script
  prints "decisions: python only" rather than inventing a probability.
- `optional_ai/claude.py` (Phase 22) — `narrate(signal)`. Requires BOTH
  `ENABLE_CLAUDE=true` AND `ANTHROPIC_API_KEY`; either alone is off. The
  response is never parsed back into the signal, the system prompt forbids
  changing or inventing numbers, and any failure (no key, no package, no
  network, rate limit, bad auth) returns `local_narration()` instead — same
  headings, built from the engine's own reason codes. `Narration.source` says
  which one you got.
- `execution/broker.py` (Phase 23) — `Broker` ABC and `PaperBroker`: limit
  orders with a bar expiry, fills against closed bars, the same pessimistic
  intrabar rule as the labeller (a bar touching both target and stop is a
  STOP, flagged `ambiguous bar: stop assumed first`), R and profit on settle,
  and a JSONL journal of every PLACED/FILLED/CLOSED/EXPIRED event.
- `execution/live.py` (Phase 24) — `LiveBroker`, disabled by default behind
  three independent gates: `ENABLE_LIVE=true`, the literal
  `confirm="I UNDERSTAND THIS PLACES REAL ORDERS"`, and a passing `preflight()`.
  Gates are re-checked on construction AND on every order, so flipping the env
  var mid-run cannot arm an object that already exists. Adds a daily-loss halt,
  a `max_positions` cap, and `reconcile()` that re-reads the terminal rather
  than trusting local memory. `preflight()` reports what a machine can check
  and names the four preconditions only you can confirm.
- `main.py paper` — replay cached bars through the paper broker, with a
  decision breakdown and, when nothing is taken, the veto codes that explain
  why. `main.py analyze --narrate` adds the written explanation.
  `main.py preflight` prints the live-gate report and exits 1 while any gate
  is shut.
- `.env.example` — `ENABLE_LIVE` and `DB_PATH`.

### Changed
- `DATA_DIR` now relocates the setup census too (`db_path` follows it) unless
  `DB_PATH` is set explicitly. Previously a relocated cache still queried the
  default database, which is how a test run silently borrowed the real
  18,539-bar census.
- `main.py status` reports the live-trading and narration switches.

### Verified
- 394/394 tests pass.
- `test_the_analysis_pipeline_does_not_import_live` inspects the source of
  `features/context.py` and `signals/decision_engine.py` and fails if either
  ever mentions `execution.live` or `LiveBroker`. Deleting `execution/` from
  disk leaves the analysis system working.
- All three live gates raise `LiveTradingDisabled` in isolation; the right
  phrase with the flag off still raises, and so does the flag with no phrase.
- `narrate()` with no key, and with a bogus key and no network, both return
  `source="local"` with the full six-heading text and the error recorded.
- `python main.py paper --symbol XAUUSDm --tf M5 --bars 2500` on the sample
  history: 618 candidates, **0 orders placed** — 484 vetoed LOW_RELIABILITY_LOW,
  134 VERY_LOW. Dropping the gate to `--min-reliability VERY_LOW` still takes
  nothing: 250 HTF_CONFLICT, 235 BELOW_THRESHOLDS, 133 NEGATIVE_EXPECTANCY,
  with observed rates around p=0.24-0.36. That is the intended answer on this
  data, not a broken command.

### Not verified here
- The Pine script's parity with the Python engine. TradingView cannot be
  scripted from CI, so it is checked by eye or not at all; the file says so.
- `LiveBroker`'s order path. It needs Windows, MetaTrader5 and a real account,
  none of which exist in this container. Every gate around it is tested; the
  `mt5.order_send` call itself is not.
- The Claude request path (no key here). Only the fallback is exercised.

## [0.13.0] — 2026-08-19 — Phases 17-20: chart, backtest, walk-forward, Monte Carlo

### Added
- `visualization/chart.py` — offline Plotly HTML: candles, swings, breaks,
  liquidity, sweeps, order blocks, FVGs, premium/discount and the signal panel.
  Everything drawn is filtered to `as_of`.
- `backtesting/engine.py`, `backtesting/metrics.py` — DETERMINISTIC mode takes
  every candidate; DECISION mode takes only what the decision engine approved
  from probabilities queried as-of each signal. `assert_no_lookahead()` audits
  a finished run for entering on/before the signal bar and for resolving
  before entering.
- `backtesting/walk_forward.py` — expanding chronological folds with a purge
  gap of `max_hold_bars`, out-of-sample metrics and per-fold calibration.
- `backtesting/monte_carlo.py` — IID and block resampling of realised R, with
  drawdown, losing-streak and risk-of-ruin distributions.

### Changed
- Drawdown is reported twice and separately: `max_drawdown_r` from the R curve,
  `max_drawdown_pct` from the account curve. A percentage of a curve that can
  cross zero is meaningless, and it was reading 129.9%.

### Verified
- 379/379 tests pass. Sample history: 168 trades, 30.4% win rate, +0.038R
  expectancy, PF 1.05 — noise, which is what a near-random synthetic should give.

### Noted
- Monte Carlo median final R is +7.5 under IID but **-1.0 under block
  resampling**. The gap is serial dependence between overlapping setups showing
  up on its own, exactly as PROBABILITY_METHODOLOGY.md predicted. Trust the
  block number.

## [0.12.0] — 2026-08-19 — Phases 14-16: probability, confluence, decision

### Added
- `probability/historical_stats.py` — five similarity tiers (T1-T5) with
  back-off; every query filtered on `resolved_at < signal_time`, so a trade
  still open when the signal fired contributes nothing. The tier actually used
  travels with the estimate.
- `probability/probability.py` — beta-binomial posterior with a Jeffreys prior
  over recency-weighted counts, a Wilson interval on raw counts, and a
  moving-block bootstrap once n is large enough. When the bootstrap comes back
  much wider, the analytic interval is understating dependence and is
  DISCARDED, not quietly reported. Nothing returns a bare number: sample size,
  effective sample size, interval, tier and reliability travel with it.
- `probability/calibration.py` — reliability diagram, Brier, log loss, ECE, a
  base-rate baseline, and isotonic correction fitted on validation only.
- `signals/confluence.py` — the 100 points of confluence, itemised, each
  component scoring continuously. Never converted into a probability.
- `signals/decision_engine.py` — hard vetoes first (insufficient sample, low
  reliability, R:R below minimum, negative expectancy, stop too wide, HTF
  conflict), then tiering on probability AND score AND R:R AND reliability
  together. Every decision carries reason codes and is auditable without
  re-running the engine.
- `main.py analyze`.

### Verified
- 359/359 tests pass. On real data `analyze` returns NO_TRADE at 36.4% from 57
  samples, LOW reliability, score 25/100 — the system working, not failing.

## [0.11.0] — 2026-08-19 — Phase 13: setups, labelling and the census

### Added
- `risk/levels.py` — stops from structure with an ATR buffer (never fixed
  pips), targets preferring real liquidity with an R-multiple fallback, and
  refusals with reasons (STOP_ON_WRONG_SIDE, STOP_TOO_WIDE, RR_TOO_LOW).
  Position size rounds DOWN to the broker step.
- `signals/setups.py` — the five pre-registered families only, built from
  `MarketContext.at(t)` so `signal_index` is the first issuable bar.
  Overlapping candidates are flagged superseded, not deleted: the census stays
  complete while the estimates stay independent.
- `backtesting/labeling.py` — fill test first, intrabar TP+SL ambiguity
  resolved as SL_FIRST and flagged, spread and slippage removed at labelling
  time so a gross-only edge cannot survive, plus MAE/MFE in R.
- `database/models.py` — SQLite census (setups, outcomes, runs).
- `main.py build-db`; `tests/conftest.py` gains `make_market_frame()`, because
  a plain random walk contains almost no FVGs, order blocks or sweeps. Test
  scaffolding — never a probability input.

### Verified
- 330/330 tests pass. 18,539 bars → 7,273 candidates (247 non-overlapping),
  labelled and stored in 23 s.

### Noted
- A full-history census costs ~20 s; the cost is the nearest-object scans in
  feature extraction. Acceptable for a one-off build, and it is why Phase 18
  reuses one `MarketContext`.

## [0.10.0] — 2026-08-19 — Phases 11-12: multi-timeframe, regime, features

### Added
- `context/mtf_bias.py` — `resample_frame()` drops an incomplete final bucket
  so a forming HTF bar cannot appear, and `align_htf()` maps each LTF bar to
  the newest HTF bar that had actually CLOSED by then. Reading an H1 close on
  the 09:05 M5 bar is the most common way an SMC backtest fools itself.
- `context/market_regime.py` — ATR percentile × ADX signed by bias → a
  `regime_key` like `TREND_DOWN|HIGH_VOL`, for grouping rather than filtering.
- `common/indicators.py` — Wilder ±DI/ADX and a rolling percentile helper.
- `features/context.py` — `MarketContext` builds every detector once and
  exposes `at(t)`; because each series is already as-of honest, `at(t)` equals
  a fresh build over `frame[:t+1]`, asserted rather than assumed.
- `features/feature_engineering.py` — ~65 features across context, MTF,
  structure, liquidity, points of interest and trade geometry. Absent objects
  yield NaN, never 0.0, because a zero reads as a real measurement.

### Verified
- 310/310 tests pass.

## [0.9.0] — 2026-08-19 — Phases 9-10: order blocks and premium/discount

### Added
- `orderblocks/order_blocks.py` — the last candle closing against a
  structure-breaking leg, anchored to its BOS or MSS and known only at that
  break bar. Zone modes FULL_RANGE / BODY / WICK_TO_BODY. Lifecycle FRESH →
  TOUCHED → MITIGATED → INVALIDATED → BREAKER with monotone fill depth, so any
  past bar's state is reproducible. A block dies on a CLOSE through it, not a
  wick; a dead block retested from the other side flips to a breaker.
- `context/premium_discount.py` — the dealing range between the confirmed
  structural low and high, DISCOUNT / EQUILIBRIUM / PREMIUM zones, an optional
  OTE band, and a NO_RANGE verdict when the range is narrower than
  `min_range_atr` rather than splitting noise into percentages.

### Verified
- 290/290 tests pass, including per-bar truncation tests for both.

## [0.8.0] — 2026-08-19 — Phases 7-8: displacement runs and imbalance

### Added
- Displacement v2: `displacement_run_at()` scores the best run ENDING at bar i
  (up to `max_run_bars` back), never using anything after i. Three 0.4-ATR
  pushes in a row now register as the leg they are. BOS/CHOCH/MSS all score runs.
- `imbalance/fvg.py` — the three-bar gap with a full lifecycle. Fill depth is
  monotone by construction, so FRESH → PARTIAL → MITIGATED (at consequent
  encroachment) → INVALIDATED is reproducible at any past bar.
- `imbalance/ifvg.py` — a gap killed by a CLOSE beyond it and then reclaimed
  within a window flips polarity, keeping the original range and a link back to
  its origin. Confirmed at the reclaim bar.

### Changed
- The displacement imbalance component is switched on and the weights move to
  the SMC_DEFINITIONS 0.40 / 0.20 / 0.20 / 0.20. **This changes `rules_hash`**,
  which is what keeping them in config rather than in code is for.

### Verified
- 271/271 tests pass, including a per-bar truncation test for gaps.
- 18,539 bars → 761 FVGs (443 rejected as too small), 609 inversions, 0.88 s.

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
