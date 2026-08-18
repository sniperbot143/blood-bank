# Probability Methodology (v0.1)

The core claim of this system is: *"setups that look like this one have historically
reached TP1 before SL X % of the time, out of N comparable cases."* Everything below
exists to make that sentence defensible.

**Score ≠ probability.** The confluence score measures how much evidence is present
now. The probability measures what happened historically to setups with that
evidence. They are computed by different code paths and are never mapped onto each other.

---

## 1. Building the historical setup database

Replay bars strictly forward. At each closed bar `t`, the setup builder emits any
candidate setup that becomes valid, with its entry, SL, TP1, TP2, TP3 computed by
the same functions the live engine uses. Both winners and losers are stored — the
database is a census of candidates, not a highlight reel.

One row per candidate:

```
setup_id, symbol, timeframe, direction, setup_type, signal_time (bar close),
entry, sl, tp1, tp2, tp3, rr1, rr2, features (JSON + indexed columns),
regime_key, session, config_hash, code_sha
```

**De-overlapping.** Only one setup per (symbol, direction) may be open at a time;
later candidates while one is open are stored with `superseded = true` and excluded
from probability estimation. Without this, overlapping M5 setups double-count the
same price move and inflate the effective sample size.

---

## 2. Outcome labeling

Walk forward from `signal_time` until one of:

| label | condition |
|---|---|
| `TP1_FIRST` | TP1 touched before SL |
| `TP2_FIRST` | TP2 touched before SL (implies TP1 first) |
| `SL_FIRST` | SL touched first |
| `TIMEOUT` | neither hit within `max_hold_bars` (default 96 on M5) → close at market, R recorded |
| `INVALIDATED` | structural invalidation rule fires before entry is filled |

Entry model: limit order at the POI, valid for `entry_valid_bars` (default 12);
unfilled candidates are labelled `NO_FILL` and excluded from win-rate but kept for
fill-rate statistics.

Also recorded: `r_multiple` (net of costs), `mae_r`, `mfe_r`, `bars_to_result`,
`resolved_at`, `intrabar_ambiguous` flag.

Costs are applied at labeling time: entry slippage, spread at fill (from the bar's
`spread` column), and a configurable commission per lot. A setup that is only
profitable gross is a losing setup here.

---

## 3. Similarity: hierarchical back-off, not a magic distance

To estimate probability for a new setup we need comparable historical setups. We use
**tiered exact matching with back-off**, which is transparent and needs no ML:

| tier | match key | typical N |
|---|---|---|
| T1 | symbol + timeframe + setup_type + direction + regime_key + session + PD zone | small |
| T2 | symbol + timeframe + setup_type + direction + regime_key | medium |
| T3 | symbol + timeframe + setup_type + direction | larger |
| T4 | symbol + timeframe + setup_family (e.g. all SWEEP_MSS_*) | largest |
| T5 | all symbols of the same asset class + timeframe + setup_family | fallback only |

Start at T1. If `N < min_samples` (default 30 usable, resolved, non-superseded),
back off one tier. Report which tier was used — a T4 answer is honest, a T1 answer
with N = 7 is not.

Within the selected tier, continuous features (sweep magnitude/ATR, OB size/ATR,
distance to opposing liquidity, RR) are used two ways:
1. **Recency weighting:** weight `w_i = exp(-Δdays / half_life)` with
   `half_life = 365` days (configurable). Non-stationarity is real; old data counts less.
2. **Optional kNN refinement (Phase 14b, only if T1/T2 have data):** Gower distance
   over standardized features, `k = max(30, 0.1·N)`, compared against the tier
   estimate. If kNN doesn't beat the tier estimate on out-of-sample log-loss, it is dropped.

---

## 4. The estimator

Weighted successes `s = Σ w_i·1[TP1 first]`, weighted trials `n = Σ w_i` over
resolved, non-superseded, filled setups.

**Beta-binomial posterior with a Jeffreys prior** `Beta(0.5, 0.5)`:

```
p̂ = (s + 0.5) / (n + 1)                     posterior mean
95 % credible interval = Beta.ppf(0.025 | s+0.5, n+0.5) … Beta.ppf(0.975 | …)
```

Reported alongside: the **Wilson score interval** on the unweighted counts, and —
when `n >= 100` — a **moving-block bootstrap** CI (block length = median holding
time in bars) which does not assume independence. When the bootstrap CI is more
than 1.5× wider than the analytic one, the analytic one is discarded and the
bootstrap is reported. Overlapping-trade dependence is the most likely way to fool
ourselves; this is the check for it.

Why Jeffreys rather than Laplace `Beta(1,1)`: it is the reference prior for a
binomial proportion, shrinks less aggressively at moderate `n`, and is standard.
Configurable via `probability_config.prior`.

The same machinery produces `P(TP2 before SL)` and `P(R > 0)`; expectancy is
reported as weighted mean R with its own bootstrap CI, because a 66 % TP1 rate at
1:1 and a 45 % rate at 1:3 are different businesses.

---

## 5. Reliability tiers

```
n_eff = n²/Σw²            (Kish effective sample size)
ci_w  = upper − lower     (95 % CI width)

VERY_HIGH : n_eff >= 500 and ci_w <= 0.06 and tier <= T2 and recency_ok
HIGH      : n_eff >= 200 and ci_w <= 0.10 and tier <= T3
MEDIUM    : n_eff >=  60 and ci_w <= 0.16
LOW       : n_eff >=  30
VERY_LOW  : n_eff <   30   → probability reported but decision forced to NO_TRADE
```

`recency_ok` = at least 25 % of the weighted mass comes from the last 12 months.
Anything below MEDIUM cannot produce a STRONG decision, whatever the score says.

---

## 6. Calibration

A probability that says 70 % must be right ~70 % of the time. Every walk-forward run
produces:
- **Reliability diagram** (10 bins, predicted vs realized) written as a Plotly HTML.
- **Brier score** and **log loss** vs two baselines: base rate, and the raw setup
  score/100. If our probability doesn't beat the base rate out-of-sample, the
  probability engine adds nothing and we say so.
- **Expected Calibration Error (ECE)**; target < 0.05.
- Optional post-hoc **isotonic regression** or **Platt scaling**, fitted on the
  validation fold only and applied to OOS — never fitted on the data it corrects.

---

## 7. No look-ahead in probability (mandatory)

1. Comparable setups are filtered by `resolved_at < current_signal_time`. Not
   `signal_time <` — an unresolved trade's outcome was unknown at the time.
2. Walk-forward with purge: split chronologically into TRAIN / VALIDATION / OOS with
   a purge gap ≥ `max_hold_bars`; expanding-window re-estimation, step = 1 month.
3. Any threshold or calibration map is fitted on TRAIN/VALIDATION and evaluated on OOS
   exactly once per pre-registered hypothesis. Re-tuning on OOS results converts
   the OOS set into a training set, and the run is marked `TAINTED` in the `runs` table.
4. The unit test `test_no_lookahead.py` asserts that the probability returned for a
   historical bar is unchanged when the engine is fed the truncated history.

---

## 8. Output contract

```json
{
  "tp1_probability": 0.684,
  "tp2_probability": 0.412,
  "expectancy_r": 0.31,
  "median_r": 0.18,
  "sample_size": 1842,
  "effective_sample_size": 913,
  "historical_win_rate": 0.661,
  "confidence_interval_95": [0.638, 0.709],
  "ci_method": "beta_jeffreys+block_bootstrap",
  "probability_reliability": "HIGH",
  "similarity_tier": "T2",
  "setup_type": "BUYSIDE_SWEEP_BEARISH_MSS_OB_FVG",
  "symbol": "XAUUSDm",
  "timeframe": "M5",
  "market_regime": "TREND_DOWN|HIGH_VOL",
  "session": "LONDON",
  "lookback_period": "2019-01-01..2026-08-01",
  "recency_half_life_days": 365,
  "config_hash": "9f2c…"
}
```

If `sample_size` is below `min_samples` at every tier, the fields are still returned
with `probability_reliability: "VERY_LOW"` and `probability_source: "INSUFFICIENT_DATA"`,
and the decision engine returns `NO_TRADE` with reason `INSUFFICIENT_SAMPLE`.

---

## 9. Decision engine

Inputs: `p = tp1_probability`, `reliability`, `score` (0–100), `rr1`,
`regime_conflict` flag, `htf_conflict` flag.

**Hard vetoes (any one ⇒ `NO_TRADE`, with a reason code):**
- `reliability ∈ {VERY_LOW, LOW}` for STRONG/normal decisions
- `rr1 < min_rr` (default 1.5) after costs
- `p * rr_net − (1 − p) < 0` — negative expectancy, regardless of how pretty the setup is
- HTF bias directly opposes the setup and `htf_veto = true`
- SL distance `< broker stops level` or `> max_sl_atr * ATR` (default 3.0)
- economic-blackout window (optional local CSV calendar, no paid API)
- structural invalidation already triggered on the signal bar

**Tiering (v0.1 starting values, to be optimized on TRAIN/VALIDATION only):**

| decision | conditions |
|---|---|
| `STRONG_BUY/SELL` | `p >= 0.72` **and** `score >= 85` **and** `rr1 >= 2.0` **and** reliability ≥ HIGH |
| `BUY/SELL` | `p >= 0.63` **and** `score >= 70` **and** `rr1 >= 1.5` **and** reliability ≥ MEDIUM |
| `WEAK_BUY/SELL` | `p >= 0.55` **and** `score >= 55` **and** `rr1 >= 1.5` **and** reliability ≥ MEDIUM |
| `NO_TRADE` | anything else, or any veto |

Every decision carries `reason_codes: []` — the exact conditions that produced it,
so any signal can be audited without re-running the engine.

**Setup score weights** (deterministic, sums to 100): HTF bias 15, liquidity sweep 20,
MSS 20, displacement 10, OB 10, FVG 10, premium/discount 5, session 5, R:R 5. Each
component is scored continuously (e.g. displacement contributes
`10 × displacement_score`) rather than as a 0/1 flag, so the score is smooth.

The score's *only* role in the decision is as a co-condition and as a similarity
feature. It never becomes a probability.
