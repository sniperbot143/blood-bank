"""From comparable outcomes to a probability -- with its sample size attached.

Estimator (docs/PROBABILITY_METHODOLOGY.md §4): a beta-binomial posterior with
a Jeffreys prior over recency-weighted counts, reported alongside a Wilson
interval on the raw counts and, when there is enough data, a moving-block
bootstrap that does not assume independence.

Why the bootstrap matters here: overlapping M5 setups are not independent
samples, so the analytic interval is too narrow. When the block bootstrap
comes back much wider, the analytic one is understating dependence and is
discarded rather than quietly reported.

Nothing in this module is allowed to return a bare number. Every estimate
carries its sample size, its interval, the similarity tier it came from and a
reliability grade -- that is the difference between a probability and a guess.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from config.probability_config import (
    DEFAULT_PROBABILITY_CONFIG,
    Prior,
    ProbabilityConfig,
    Reliability,
)
from database.models import SetupStore
from probability.historical_stats import (
    ComparableSet,
    SimilarityKey,
    find_comparables,
    outcome_flags,
)

try:  # SciPy gives exact Beta quantiles; the fallback keeps the core dependency-light
    from scipy.stats import beta as _beta
except Exception:  # pragma: no cover
    _beta = None


@dataclass
class ProbabilityEstimate:
    """A probability that can be audited: every input is on the object."""

    probability: float
    label: str                       # what is being estimated, e.g. "tp1"
    sample_size: int
    effective_sample_size: float
    historical_win_rate: float
    confidence_interval_95: tuple[float, float]
    ci_method: str
    reliability: Reliability
    similarity_tier: str
    tier_counts: dict[str, int] = field(default_factory=dict)
    expectancy_r: float = float("nan")
    median_r: float = float("nan")
    mean_mae_r: float = float("nan")
    mean_mfe_r: float = float("nan")
    source: str = "HISTORICAL_SIMILAR_SETUPS"
    key: SimilarityKey | None = None
    as_of: pd.Timestamp | None = None
    lookback_period: str = ""
    recent_mass: float = float("nan")
    config_hash: str = ""

    @property
    def ci_width(self) -> float:
        low, high = self.confidence_interval_95
        return float(high - low)

    @property
    def is_usable(self) -> bool:
        return self.reliability.at_least(Reliability.MEDIUM)

    def as_dict(self) -> dict:
        return {
            f"{self.label}_probability": round(float(self.probability), 4),
            "sample_size": self.sample_size,
            "effective_sample_size": round(float(self.effective_sample_size), 1),
            "historical_win_rate": round(float(self.historical_win_rate), 4),
            "confidence_interval_95": [round(float(x), 4) for x in self.confidence_interval_95],
            "ci_method": self.ci_method,
            "probability_reliability": self.reliability.value,
            "probability_source": self.source,
            "similarity_tier": self.similarity_tier,
            "tier_counts": self.tier_counts,
            "expectancy_r": round(float(self.expectancy_r), 4),
            "median_r": round(float(self.median_r), 4),
            "setup_type": self.key.setup_type if self.key else "",
            "symbol": self.key.symbol if self.key else "",
            "timeframe": self.key.timeframe if self.key else "",
            "market_regime": self.key.regime_key if self.key else "",
            "session": self.key.session if self.key else "",
            "lookback_period": self.lookback_period,
            "config_hash": self.config_hash,
        }


def insufficient(label: str, key: SimilarityKey | None = None,
                 tier_counts: dict[str, int] | None = None,
                 config: ProbabilityConfig = DEFAULT_PROBABILITY_CONFIG) -> ProbabilityEstimate:
    """The honest answer when there is nothing to estimate from."""
    return ProbabilityEstimate(
        probability=float("nan"), label=label, sample_size=0, effective_sample_size=0.0,
        historical_win_rate=float("nan"), confidence_interval_95=(0.0, 1.0),
        ci_method="NONE", reliability=Reliability.VERY_LOW, similarity_tier="NONE",
        tier_counts=tier_counts or {}, source="INSUFFICIENT_DATA", key=key,
        config_hash=config.config_hash,
    )


# ------------------------------------------------------------------ intervals

def wilson_interval(successes: int, trials: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval -- well behaved at small n and near 0/1."""
    if trials <= 0:
        return 0.0, 1.0
    phat = successes / trials
    denominator = 1 + z**2 / trials
    centre = (phat + z**2 / (2 * trials)) / denominator
    margin = z * np.sqrt(phat * (1 - phat) / trials + z**2 / (4 * trials**2)) / denominator
    return float(max(0.0, centre - margin)), float(min(1.0, centre + margin))


def beta_interval(successes: float, trials: float, prior: Prior) -> tuple[float, float]:
    """Equal-tailed 95% credible interval for the posterior."""
    a0, b0 = _prior_parameters(prior)
    a = successes + a0
    b = (trials - successes) + b0
    if a <= 0 or b <= 0:
        return 0.0, 1.0
    if _beta is not None:
        return float(_beta.ppf(0.025, a, b)), float(_beta.ppf(0.975, a, b))

    # Normal approximation to the Beta, used only when SciPy is absent.
    mean = a / (a + b)
    variance = a * b / ((a + b) ** 2 * (a + b + 1))
    spread = 1.96 * float(np.sqrt(variance))
    return float(max(0.0, mean - spread)), float(min(1.0, mean + spread))


def _prior_parameters(prior: Prior) -> tuple[float, float]:
    if prior is Prior.JEFFREYS:
        return 0.5, 0.5
    if prior is Prior.LAPLACE:
        return 1.0, 1.0
    return 0.0, 0.0


def block_bootstrap_interval(
    successes: np.ndarray, block_length: int, config: ProbabilityConfig
) -> tuple[float, float]:
    """Moving-block bootstrap: resample blocks, not individual trades.

    Overlapping setups share price action, so shuffling single outcomes would
    pretend they are independent. Blocks keep neighbours together.
    """
    n = len(successes)
    if n < 2:
        return 0.0, 1.0
    block = max(1, min(int(block_length), n))
    rng = np.random.default_rng(config.bootstrap_seed)
    starts_available = n - block + 1
    draws = int(np.ceil(n / block))

    means = np.empty(config.bootstrap_iterations, dtype="float64")
    for i in range(config.bootstrap_iterations):
        starts = rng.integers(0, starts_available, size=draws)
        sample = np.concatenate([successes[s:s + block] for s in starts])[:n]
        means[i] = sample.mean()
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


# ---------------------------------------------------------------- estimation

def _weights(rows: pd.DataFrame, as_of: pd.Timestamp | None,
             config: ProbabilityConfig) -> np.ndarray:
    n = len(rows)
    if not config.recency_weighting or as_of is None or "resolved_at" not in rows.columns:
        return np.ones(n)
    resolved = pd.to_datetime(rows["resolved_at"], utc=True, errors="coerce")
    age_days = (pd.Timestamp(as_of) - resolved).dt.total_seconds() / 86400.0
    age_days = age_days.fillna(0.0).clip(lower=0.0).to_numpy("float64")
    return np.exp(-age_days / config.recency_half_life_days)


def _reliability(
    n_eff: float, ci_width: float, tier: str, recent_mass: float,
    config: ProbabilityConfig,
) -> Reliability:
    tier_rank = int(tier[1]) if tier.startswith("T") and tier[1:].isdigit() else 9
    recent_ok = not np.isfinite(recent_mass) or recent_mass >= config.recent_mass_required

    if (n_eff >= config.very_high_n and ci_width <= config.very_high_ci
            and tier_rank <= 2 and recent_ok):
        return Reliability.VERY_HIGH
    if n_eff >= config.high_n and ci_width <= config.high_ci and tier_rank <= 3:
        return Reliability.HIGH
    if n_eff >= config.medium_n and ci_width <= config.medium_ci:
        return Reliability.MEDIUM
    if n_eff >= config.low_n:
        return Reliability.LOW
    return Reliability.VERY_LOW


def estimate_from_rows(
    rows: pd.DataFrame,
    label: str,
    successes_mask: pd.Series,
    *,
    tier: str = "T3",
    tier_counts: dict[str, int] | None = None,
    key: SimilarityKey | None = None,
    as_of: pd.Timestamp | None = None,
    config: ProbabilityConfig = DEFAULT_PROBABILITY_CONFIG,
) -> ProbabilityEstimate:
    """Estimate one probability from a set of comparable outcomes."""
    n = len(rows)
    if n == 0:
        return insufficient(label, key, tier_counts, config)

    successes = successes_mask.to_numpy(dtype=bool)
    weights = _weights(rows, as_of, config)
    weighted_successes = float((weights * successes).sum())
    weighted_trials = float(weights.sum())

    a0, b0 = _prior_parameters(config.prior)
    denominator = weighted_trials + a0 + b0
    probability = ((weighted_successes + a0) / denominator) if denominator > 0 else float("nan")

    n_eff = float(weights.sum() ** 2 / np.square(weights).sum()) if weights.sum() > 0 else 0.0
    raw_successes = int(successes.sum())

    analytic = beta_interval(weighted_successes, weighted_trials, config.prior)
    wilson = wilson_interval(raw_successes, n)
    interval, method = analytic, f"beta_{config.prior.value.lower()}"

    if n >= config.bootstrap_min_samples:
        block = 1
        if "bars_to_result" in rows.columns:
            median_bars = pd.to_numeric(rows["bars_to_result"], errors="coerce").median()
            if np.isfinite(median_bars) and median_bars > 0:
                block = int(max(2, min(n // 5, round(median_bars / 10) + 1)))
        boot = block_bootstrap_interval(successes.astype(float), block, config)
        analytic_width = analytic[1] - analytic[0]
        boot_width = boot[1] - boot[0]
        if analytic_width > 0 and boot_width / analytic_width > config.bootstrap_width_ratio:
            # The analytic interval is understating dependence -- do not use it.
            interval, method = boot, "block_bootstrap"
        else:
            method = f"beta_{config.prior.value.lower()}+block_bootstrap"

    recent_mass = float("nan")
    lookback = ""
    if as_of is not None and "resolved_at" in rows.columns and weighted_trials > 0:
        resolved = pd.to_datetime(rows["resolved_at"], utc=True, errors="coerce")
        cutoff = pd.Timestamp(as_of) - pd.DateOffset(months=config.recent_mass_months)
        recent_mass = float(weights[(resolved >= cutoff).to_numpy()].sum() / weighted_trials)
        if resolved.notna().any():
            lookback = f"{resolved.min():%Y-%m-%d}..{resolved.max():%Y-%m-%d}"

    reliability = _reliability(n_eff, interval[1] - interval[0], tier, recent_mass, config)

    r = pd.to_numeric(rows.get("r_multiple", pd.Series(dtype=float)), errors="coerce")
    mae = pd.to_numeric(rows.get("mae_r", pd.Series(dtype=float)), errors="coerce")
    mfe = pd.to_numeric(rows.get("mfe_r", pd.Series(dtype=float)), errors="coerce")

    return ProbabilityEstimate(
        probability=float(probability), label=label, sample_size=n,
        effective_sample_size=n_eff,
        historical_win_rate=float(raw_successes / n),
        confidence_interval_95=(float(interval[0]), float(interval[1])),
        ci_method=method, reliability=reliability, similarity_tier=tier,
        tier_counts=tier_counts or {},
        expectancy_r=float(np.average(r.fillna(0.0), weights=weights)) if len(r) else float("nan"),
        median_r=float(r.median()) if len(r) else float("nan"),
        mean_mae_r=float(mae.mean()) if len(mae) else float("nan"),
        mean_mfe_r=float(mfe.mean()) if len(mfe) else float("nan"),
        key=key, as_of=as_of, lookback_period=lookback, recent_mass=recent_mass,
        config_hash=config.config_hash,
    )


def estimate_probabilities(
    store: SetupStore,
    key: SimilarityKey,
    *,
    as_of: pd.Timestamp | None = None,
    config: ProbabilityConfig = DEFAULT_PROBABILITY_CONFIG,
    comparables: ComparableSet | None = None,
) -> dict[str, ProbabilityEstimate]:
    """P(TP1 before SL), P(TP2 before SL) and P(R > 0) for one setup."""
    found = comparables or find_comparables(store, key, as_of=as_of, config=config)
    if found.rows.empty:
        return {name: insufficient(name, key, found.tier_counts, config)
                for name in ("tp1", "tp2", "positive_r")}

    flags = outcome_flags(found.rows)
    return {
        name: estimate_from_rows(
            found.rows, name, mask, tier=found.tier, tier_counts=found.tier_counts,
            key=key, as_of=as_of, config=config,
        )
        for name, mask in flags.items()
    }
