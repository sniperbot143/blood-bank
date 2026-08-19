# SMC Trading Intelligence

A free-first, local, deterministic + probabilistic Smart Money Concepts analysis
engine. It reads market data, detects SMC structure, scores confluence, estimates
outcome probabilities from its own historical database, and issues a decision:

```
STRONG_BUY | BUY | WEAK_BUY | NO_TRADE | WEAK_SELL | SELL | STRONG_SELL
```

with a probability, a sample size, a confidence interval and a reliability tier
attached to every number.

## Current status

**All 24 phases complete. 408 tests passing.**

| phase | scope | status |
|---|---|---|
| 0 | Architecture, definitions, methodology | complete |
| 1 | Data ingestion & normalization | complete |
| 2 | Swing detection | complete |
| 3 | Market structure (HH/HL/LH/LL, bias) | complete |
| 4 | BOS / CHOCH / MSS | complete |
| 5 | Liquidity pools, equal levels & sessions | complete |
| 6 | Liquidity sweeps | complete |
| 7 | Displacement runs (multi-bar legs) | complete |
| 8 | Fair value gaps & inversions | complete |
| 9 | Order blocks, mitigation, breakers | complete |
| 10 | Premium / discount & the dealing range | complete |
| 11 | Multi-timeframe alignment & market regime | complete |
| 12 | Market context and ~65 features | complete |
| 13 | Setup builder, outcome labelling, the census | complete |
| 14 | Probability engine (tiers, intervals, reliability) | complete |
| 15 | Confluence score (100 points, itemised) | complete |
| 16 | Decision engine (vetoes, then tiering) | complete |
| 17 | Offline chart | complete |
| 18 | Backtester + metrics | complete |
| 19 | Purged walk-forward | complete |
| 20 | Monte Carlo | complete |
| 21 | TradingView viewer (Pine v6) | complete — **parity unverified**, see below |
| 22 | Optional Claude narration | complete — fallback path verified only |
| 23 | Paper trading | complete |
| 24 | Live execution | complete — **disabled by default, order path unverified** |

Three things in this repository cannot be verified from a Linux CI container and
are not claimed to be: the MT5 fetch path (Windows only), the Pine script's
agreement with the Python engine (TradingView cannot be scripted), and
`LiveBroker.place()` itself (needs a real terminal and account). Everything
around them — the gates, the fallbacks, the read-only assertions — is tested.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                 # optional; defaults work

python main.py status                                # environment + cache state

# With MT5 (Windows, terminal open and logged in):
python main.py symbols --search XAU
python main.py ingest  --symbol XAUUSDm --tf M5 --bars 200000

# Without MT5 (any OS) -- free real history from HistData.com.
# Their MT export is headerless and in New York time (EST/EDT, WITH DST), so
# name the layout and give the offset for the month: -4 in summer, -5 in winter.
python main.py ingest --symbol XAUUSD --tf M1 \
    --csv data/raw/DAT_MT_XAUUSD_M1_202607.csv \
    --format histdata_mt --offset -4 --digits 3 --on-duplicate widest
python main.py resample --symbol XAUUSD --source M1 --tf M5 --digits 3

# Or generated sample data, for exercising the pipeline with no download:
python tools/make_synthetic_csv.py --out data/raw/XAUUSDm_M5.csv
python main.py ingest  --symbol XAUUSDm --tf M5 --csv data/raw/XAUUSDm_M5.csv --digits 2
python main.py inspect --symbol XAUUSDm --tf M5
python main.py swings  --symbol XAUUSDm --tf M5 --last 6
python main.py structure --symbol XAUUSDm --tf M5 --min-atr 2.0
python main.py breaks    --symbol XAUUSDm --tf M5 --last 6
python main.py liquidity --symbol XAUUSDm --tf M5 --last 4
python main.py sweeps    --symbol XAUUSDm --tf M5 --last 4

# Build the census, then ask it something:
python main.py build-db  --symbol XAUUSDm --tf M5
python main.py analyze   --symbol XAUUSDm --tf M5            # add --narrate for words
python main.py chart     --symbol XAUUSDm --tf M5 --with-signal

# Validate before believing any of it:
python main.py backtest    --symbol XAUUSDm --tf M5 --backtest-mode DECISION
python main.py walkforward --symbol XAUUSDm --tf M5
python main.py montecarlo  --symbol XAUUSDm --tf M5

# Paper trading. Nothing here can reach a broker.
python main.py paper     --symbol XAUUSDm --tf M5 --out data/paper/positions.csv
python main.py preflight                                     # live-gate report

pytest
```

`ingest` normalizes to UTC, removes duplicates, quarantines impossible OHLC rows,
records (never fills) missing bars, drops the forming candle, and caches to parquet.
Re-running it appends only new bars and leaves existing rows byte-identical.

## Documentation

1. [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — layers, data contract, look-ahead guards, weaknesses
2. [`docs/SMC_DEFINITIONS.md`](docs/SMC_DEFINITIONS.md) — exact, computable rules for every SMC concept
3. [`docs/PROBABILITY_METHODOLOGY.md`](docs/PROBABILITY_METHODOLOGY.md) — how probabilities are earned, not asserted
4. [`docs/PHASE_1_PLAN.md`](docs/PHASE_1_PLAN.md) — the data layer, as built and verified
5. [`docs/PHASE_2_PLAN.md`](docs/PHASE_2_PLAN.md) — the swing engine and the no-repaint proof
6. [`docs/PHASE_3_PLAN.md`](docs/PHASE_3_PLAN.md) — structure labels, protected levels, bias
7. [`docs/PHASE_4_PLAN.md`](docs/PHASE_4_PLAN.md) — BOS vs CHOCH vs MSS, and displacement
8. [`docs/PHASE_5_PLAN.md`](docs/PHASE_5_PLAN.md) — liquidity pools, lifecycle and sessions
9. [`COST_AUDIT.md`](COST_AUDIT.md) — every dependency and its price (₹0 core)
10. [`docs/CHANGELOG.md`](docs/CHANGELOG.md) · [`docs/KNOWN_ISSUES.md`](docs/KNOWN_ISSUES.md)

Phases 6-24 are documented in the changelog rather than in separate plan files:
each entry states what was added, what was verified, and what was not.

## Layout

```
config/settings.py       paths, timeframes, broker offset, .env loading
config/smc_rules.py      every SMC threshold, with a rules_hash
common/indicators.py     causal primitives (true range, Wilder ATR)
data/normalizer.py       the schema gate every module reads through
data/csv_loader.py       CSV / TSV / Parquet, incl. MT5 export format
data/mt5_connector.py    read-only MT5 access (import-guarded, Windows-only)
data/cache.py            parquet cache + manifest, incremental merge
structure/swings.py      swing detection; formed vs confirmed vs superseded
structure/market_structure.py  HH/HL/LH/LL, protected levels, bias timeline
structure/displacement.py  per-bar thrust, ATR-normalised
structure/breaks.py      BOS / CHOCH / MSS, break-confirmed bias
liquidity/sessions.py    session windows, DST-correct
liquidity/levels.py      liquidity pools and their lifecycle
liquidity/sweeps.py      sweeps: penetration, rejection, magnitude
imbalance/fvg.py         fair value gaps, with a monotone fill lifecycle
imbalance/ifvg.py        inversions: a killed gap reclaimed flips polarity
orderblocks/             order blocks, mitigation and breakers
context/mtf_bias.py      higher-timeframe alignment on CLOSED bars only
context/market_regime.py ATR percentile x ADX -> a regime key
context/premium_discount.py  the dealing range and its zones
features/context.py      MarketContext: build once, ask at(t)
features/feature_engineering.py  ~65 features; absent means NaN, never 0.0
risk/levels.py           stops from structure, targets from liquidity
signals/setups.py        the five pre-registered setup families
signals/confluence.py    the 100 points of confluence, itemised
signals/decision_engine.py  vetoes first, then tiering; reason codes always
probability/             similarity tiers, beta-binomial + bootstrap, calibration
database/models.py       the SQLite setup census
backtesting/             labelling, engine, metrics, walk-forward, Monte Carlo
visualization/chart.py   offline HTML chart, filtered to as_of
tradingview/             Pine v6 viewer (no decisions, no probabilities)
optional_ai/claude.py    optional narration; falls back to local, never fails
execution/broker.py      the Broker interface and the paper implementation
execution/live.py        live orders: three gates, disabled by default
tools/                   synthetic data generator (test scaffolding, not market data)
tests/                   408 tests, incl. the no-repaint oracle tests
main.py                  ingest - analyze - chart - backtest - paper - preflight - status
```

## Ground rules

- The core system requires **no paid API, no cloud, no subscription**. Claude is an
  optional narrator that can never change a number.
- Signals are emitted on **closed bars only**; historical marks never repaint.
- **Setup score ≠ probability.** Two separate numbers, two separate code paths.
- Every probability carries its sample size. Seven examples is not evidence, and the
  engine says so instead of pretending.
- `NO_TRADE` is a valid, frequent, correct output.
- Signal generation and order execution are separate modules; live trading ships last
  and stays disabled by default.

## Live trading

It is off. Turning it on takes three deliberate, independent acts:

1. `ENABLE_LIVE=true` in `.env`
2. `LiveBroker(..., confirm="I UNDERSTAND THIS PLACES REAL ORDERS")` in code you write
3. `preflight()` passing

All three are re-checked on construction **and on every order**, so flipping the
environment variable mid-run cannot arm an object that already exists. There is
no `force=True` and no default that trades. Run `python main.py preflight` to see
what the machine can check — and the four preconditions only you can confirm:
a green out-of-sample walk-forward, 200+ paper trades whose fills match backtest
assumptions, calibration error under target, and risk limits you set on purpose.

Nothing in the analysis pipeline imports `execution/`. A test asserts it, by
reading the source of the context and decision modules. Delete the directory and
everything except live trading still runs.

## What it says on real data

One month of real XAUUSD M5 (HistData, July 2026 — 6,226 bars, 2,784 candidates,
93 of them independent):

| | |
|---|---|
| DETERMINISTIC backtest, 66 trades | 28.8% WR, +0.193R expectancy, PF 1.27 |
| DECISION backtest | **0 trades** — 59 vetoed `LOW_RELIABILITY_VERY_LOW` |
| Walk-forward, out-of-sample | **0 trades** across all 4 folds |

That is the engine working. 93 independent setups from one month of one
instrument is not a sample you can estimate a probability from, and the system
is built to say so rather than to produce a number anyway. The deterministic
row is what the setups did on overlapping trades in a single month — it is a
sanity check that the plumbing works, not an edge.

The fix is more history, not looser thresholds.
