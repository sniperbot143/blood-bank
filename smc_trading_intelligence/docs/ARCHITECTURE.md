# SMC Trading Intelligence — Architecture (Design Freeze v0.1)

Status: **DESIGN ONLY — no application code written yet.** Awaiting approval of Phase 1.

---

## 1. Design principles (non-negotiable)

1. Deterministic geometry first, statistics second, AI third.
2. Nothing in the core path requires a paid API, paid data, or a network connection.
3. A signal is only emitted on a **closed** bar. Forming bars are never evidence.
4. Setup score (confluence, 0–100) and probability (historical frequency, 0–1) are
   **two different numbers** and are never derived from each other.
5. Every probability ships with its sample size, confidence interval and reliability tier.
6. Signal generation and order execution live in different processes and different modules.
7. If the historical evidence is thin or contradictory, the correct output is `NO_TRADE`.

---

## 2. System layers

```
 ┌──────────────────────────────────────────────────────────────┐
 │ L0  SOURCES        MT5 terminal  │  CSV / Parquet archives   │
 └──────────────────────────────────────────────────────────────┘
                 │ raw OHLCV + spread + tick volume
                 ▼
 ┌──────────────────────────────────────────────────────────────┐
 │ L1  DATA          normalizer → validator → parquet cache     │
 │                   (UTC index, dedup, gap map, closed-bar     │
 │                    flag, broker suffix resolution)           │
 └──────────────────────────────────────────────────────────────┘
                 │ Bars (immutable, closed-only view)
                 ▼
 ┌──────────────────────────────────────────────────────────────┐
 │ L2  SMC DETECTION  swings → structure → BOS/CHOCH/MSS        │
 │     (pure funcs)   liquidity → sweeps → displacement         │
 │                    FVG/IFVG → order blocks/breakers          │
 │                    dealing range → premium/discount          │
 │                    sessions → market regime                  │
 └──────────────────────────────────────────────────────────────┘
                 │ typed event objects, each stamped with
                 │ detected_at_index and confirmed_at_index
                 ▼
 ┌──────────────────────────────────────────────────────────────┐
 │ L3  MTF ALIGNER    HTF bias + MTF structure + LTF trigger    │
 │                    (HTF values resampled with a lag so an    │
 │                     unfinished HTF bar cannot leak)          │
 └──────────────────────────────────────────────────────────────┘
                 │
                 ▼
 ┌──────────────────────────────────────────────────────────────┐
 │ L4  SETUP BUILDER  candidate setup + entry/SL/TP + features  │
 └──────────────────────────────────────────────────────────────┘
        │                                   │
        │ (historical replay)               │ (live/decision)
        ▼                                   ▼
 ┌────────────────────┐          ┌─────────────────────────────┐
 │ L5 OUTCOME LABELER │          │ L6 PROBABILITY ENGINE       │
 │ TP1/TP2/SL/TIMEOUT │───SQLite→│ as-of query → beta-binomial │
 │ R, MAE, MFE, dur.  │  setups  │ → Wilson/Jeffreys CI → tier │
 └────────────────────┘          └─────────────────────────────┘
                                            │
                                            ▼
                              ┌─────────────────────────────┐
                              │ L7 CONFLUENCE (score 0–100) │
                              └─────────────────────────────┘
                                            │
                                            ▼
                              ┌─────────────────────────────┐
                              │ L8 DECISION ENGINE          │
                              │ STRONG_x / x / WEAK_x /     │
                              │ NO_TRADE + reason codes     │
                              └─────────────────────────────┘
                                            │
                ┌───────────────┬───────────┴────────┬──────────────────┐
                ▼               ▼                    ▼                  ▼
          Plotly chart    JSON signal file     Backtest / WFO     Optional Claude
          (local HTML)    + SQLite journal     / Monte Carlo      narrative (off)
```

Everything from L1→L8 is importable as a library, runs offline, and is
deterministic: same bars + same config ⇒ byte-identical signal JSON.

---

## 3. Module map

```
smc_trading_intelligence/
├── config/          settings.py · smc_rules.py · probability_config.py
├── common/          indicators.py            (shared causal primitives: ATR)
├── data/            mt5_connector.py · csv_loader.py · normalizer.py · cache.py
├── structure/       swings.py · market_structure.py · bos.py · choch.py · mss.py
├── liquidity/       levels.py · equal_levels.py · sweeps.py · sessions.py
├── imbalance/       fvg.py · ifvg.py
├── orderblocks/     order_blocks.py · breakers.py · mitigation.py
├── context/         premium_discount.py · dealing_range.py · mtf_bias.py · market_regime.py
├── features/        feature_engineering.py
├── probability/     historical_stats.py · probability.py · calibration.py · confidence.py
├── signals/         confluence.py · setups.py · decision_engine.py
├── risk/            position_size.py · stop_loss.py · take_profit.py
├── backtesting/     engine.py · metrics.py · walk_forward.py · monte_carlo.py
├── visualization/   chart.py · annotations.py
├── optional_ai/     claude.py            (import-guarded, default OFF)
├── execution/       paper.py · live.py    (added Phase 23/24, default OFF)
├── database/        models.py · migrations/
├── tests/           unit/ · synthetic/ · integration/
├── docs/            this folder
├── main.py · requirements.txt · .env.example · COST_AUDIT.md · README.md
```

Dependency rule: `structure/`, `liquidity/`, `imbalance/`, `orderblocks/`,
`context/` may import only `config/`, `common/` + numpy/pandas. They never import
`data/` (no I/O in detectors) and never import each other's *engines*, only
each other's dataclasses. This keeps every detector unit-testable on a
hand-built 30-bar synthetic frame. `common/` holds primitives several detectors
need (ATR today); everything in it must be strictly causal and is tested as such.

---

## 4. Data contract

Canonical bar frame, one row per closed bar:

| column | type | notes |
|---|---|---|
| `timestamp` | datetime64[ns, UTC] | **bar OPEN time**, index, unique, monotonic |
| `open/high/low/close` | float64 | rounded to symbol `digits` |
| `tick_volume` | int64 | MT5 native |
| `real_volume` | int64 | 0 if broker doesn't provide |
| `spread` | int32 | points, MT5 native |
| `symbol` | category | broker symbol as-is, e.g. `XAUUSDm` |
| `timeframe` | category | `M1 M5 M15 H1 H4 D1` |
| `is_closed` | bool | last row is `False` in live mode |
| `gap_before` | bool | previous expected bar missing (weekend/holiday/broker outage) |

Rules enforced by the validator:
- Duplicate timestamps → keep last, log.
- `high >= max(open,close)`, `low <= min(open,close)`, else quarantine the bar.
- Missing bars are **not** forward-filled; they are recorded in a gap map, and any
  SMC pattern that would span a gap flagged `gap_before` is discarded.
- ATR, session windows and daily levels are computed in exchange/broker time
  (configurable, default `Etc/UTC` with a `broker_utc_offset` setting).

Cache: `data/cache/{symbol}/{timeframe}.parquet` + a `manifest.json` holding
first/last timestamp and a content hash, so re-runs are incremental.

---

## 5. Look-ahead prevention (the part that decides if this project is worth anything)

Five independent guards, each testable:

1. **Closed-bar rule.** The engine's public API takes `bars[:t+1]` where bar `t`
   satisfies `is_closed`. Live mode drops the forming bar before anything else runs.
2. **Confirmation index.** Every SMC object carries `formed_at_index` (where the
   pattern geometrically sits) and `confirmed_at_index` (the first bar at which it
   could be *known*). A swing high with `swing_right=2` has
   `confirmed_at_index = formed_at_index + 2`. The setup builder may only read
   objects with `confirmed_at_index <= t`. This is what makes the system
   non-repainting: historical marks never move, because they were never drawn
   before their confirmation bar.
3. **As-of probability queries.** The probability engine selects comparable setups
   with `resolved_at < current_bar_timestamp`, not `entry_time <`. A trade that was
   still open when our new signal fires contributes nothing — its outcome was
   genuinely unknown at that moment.
4. **Purged walk-forward.** Train/validation/OOS splits are chronological with a
   purge gap equal to the max trade holding time, so a training trade cannot
   overlap in time with a test trade.
5. **Intrabar ambiguity resolved pessimistically.** If a single bar's range covers
   both TP and SL, the labeler marks `SL_FIRST` unless M1 (or tick) data is loaded,
   in which case the true sequence is resolved from M1. The ambiguity flag is stored
   on the row so we can measure how much of the edge depends on it.

Test suite includes an **oracle test**: run the engine over bars `0..t` for every
`t` and assert the emitted event list for indices `< t` is identical to the list
produced by a full-history run. Any repaint fails CI.

---

## 6. Configuration

Three layers, all plain Python/TOML, no hidden constants in code:
- `config/settings.py` — paths, symbols, timeframes, timezone, broker offset, logging.
- `config/smc_rules.py` — every SMC threshold (swing_left/right, ATR filters,
  equal-level tolerance, displacement multipliers, OB validity, FVG min size,
  BOS mode, premium/discount bands).
- `config/probability_config.py` — similarity tiers, minimum samples per tier,
  prior strength, CI method, reliability thresholds, decision cutoffs.

Every emitted signal embeds a `config_hash`. A signal cannot be reproduced against
a different rule set without the mismatch being obvious.

---

## 7. Storage

SQLite (`database/smc.db`), WAL mode, accessed via SQLAlchemy Core:

- `bars_meta` — cached ranges per symbol/timeframe.
- `setups` — one row per historical candidate (winners *and* losers), with a JSON
  `features` column plus indexed columns for the fields used in similarity lookups.
- `outcomes` — TP1/TP2/SL/TIMEOUT/INVALIDATED, R, MAE, MFE, duration, ambiguity flag,
  `resolved_at`.
- `signals` — every live/paper decision as issued (immutable audit log).
- `runs` — backtest/WFO run metadata with config hash + code git SHA.

Parquet is used for bulk feature matrices; SQLite stays the system of record.

---

## 8. Runtime modes

| mode | command | writes | executes |
|---|---|---|---|
| `ingest` | `python main.py ingest --symbol XAUUSDm --tf M5` | parquet cache | no |
| `analyze` | `python main.py analyze --symbol XAUUSDm --tf M5` | signal JSON + chart | no |
| `build-db` | `python main.py build-db ...` | setups + outcomes | no |
| `backtest` | `python main.py backtest ...` | runs + metrics | no |
| `walkforward` | `python main.py walkforward ...` | OOS metrics | no |
| `paper` | Phase 23 | signals journal | simulated |
| `live` | Phase 24, `ENABLE_LIVE=false` by default and gated by a second CLI flag | orders | real |

---

## 9. Path to MT5 execution (design now, build last)

`execution/` will expose one interface, `Broker`, with `PaperBroker` and
`MT5Broker` implementations. The decision engine never imports it; a thin
`runner` process consumes signal JSON and calls the broker. That separation means
the live module can be deleted from disk and the analysis system still runs.

`MT5Broker` responsibilities: symbol spec lookup (`digits`, `point`,
`trade_stops_level`, `volume_min/step`), lot sizing from risk %, `order_send`
with deviation limits, magic number per strategy, position reconciliation on
restart, and a hard kill-switch on daily-loss breach. Preconditions before live is
ever enabled: green OOS walk-forward, ≥ 200 paper trades whose fills match
backtest assumptions within a configured slippage budget, and calibration error
under target.

---

## 10. Known weaknesses (stated up front, tracked in KNOWN_ISSUES.md)

1. **Sample-size illusion.** Overlapping setups on M5 are not independent; naive
   binomial CIs will be too narrow. Mitigation: de-overlap (one open setup at a
   time per symbol/direction) and report a block-bootstrap CI beside the analytic one.
2. **Non-stationarity.** Gold in 2019 ≠ gold in 2026. Mitigation: regime feature in
   the similarity key, recency weighting, rolling re-estimation, walk-forward only.
3. **Definition sensitivity.** SMC has no standard; results move with `swing_right`
   and displacement thresholds. Mitigation: parameter sensitivity grid reported as a
   surface, not a single tuned point. If the edge exists only at one parameter, it isn't real.
4. **Broker data quality.** Tick volume ≠ real volume; spread series is often
   synthetic on demo servers. Volume features are therefore optional and their
   contribution is measured separately.
5. **Multiple-comparison risk.** Scanning many symbols/timeframes/setup types will
   surface false positives. Mitigation: a fixed pre-registered hypothesis list per
   phase, and deflated performance metrics.
6. **Intrabar path unknown** above M1 (see §5.5).
7. **Costs.** Spread, commission and slippage on XAUUSD M5 can eat a 1:1.5 setup
   entirely. All backtests are net of a configurable cost model from day one.
