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
| 0 | Architecture, definitions, methodology | **design complete — this commit** |
| 1 | Data ingestion & normalization | proposed, awaiting approval |
| 2–24 | swings → structure → liquidity → probability → decisions → backtest → paper → live | not started |

No application code exists yet. Read the docs in this order:

1. [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — layers, data contract, look-ahead guards, weaknesses
2. [`docs/SMC_DEFINITIONS.md`](docs/SMC_DEFINITIONS.md) — exact, computable rules for every SMC concept
3. [`docs/PROBABILITY_METHODOLOGY.md`](docs/PROBABILITY_METHODOLOGY.md) — how probabilities are earned, not asserted
4. [`docs/PHASE_1_PLAN.md`](docs/PHASE_1_PLAN.md) — what gets built first
5. [`COST_AUDIT.md`](COST_AUDIT.md) — every dependency and its price (₹0 core)

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
