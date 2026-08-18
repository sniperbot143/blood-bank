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

| phase | scope | status |
|---|---|---|
| 0 | Architecture, definitions, methodology | complete |
| 1 | Data ingestion & normalization | complete |
| 2 | Swing detection | **complete — 113 tests passing** |
| 3 | Market structure (HH/HL/LH/LL, bias) | not started — awaiting approval |
| 4–24 | BOS/CHOCH/MSS → liquidity → probability → decisions → backtest → paper → live | not started |

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                 # optional; defaults work

python main.py status                                # environment + cache state

# With MT5 (Windows, terminal open and logged in):
python main.py symbols --search XAU
python main.py ingest  --symbol XAUUSDm --tf M5 --bars 200000

# Without MT5 (any OS) -- from a CSV export, or generated sample data:
python tools/make_synthetic_csv.py --out data/raw/XAUUSDm_M5.csv
python main.py ingest  --symbol XAUUSDm --tf M5 --csv data/raw/XAUUSDm_M5.csv --digits 2
python main.py inspect --symbol XAUUSDm --tf M5
python main.py swings  --symbol XAUUSDm --tf M5 --last 6

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
6. [`COST_AUDIT.md`](COST_AUDIT.md) — every dependency and its price (₹0 core)
7. [`docs/CHANGELOG.md`](docs/CHANGELOG.md) · [`docs/KNOWN_ISSUES.md`](docs/KNOWN_ISSUES.md)

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
tools/                   synthetic data generator (test scaffolding, not market data)
tests/                   113 tests, incl. the no-repaint oracle test
main.py                  ingest · inspect · swings · symbols · status
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
