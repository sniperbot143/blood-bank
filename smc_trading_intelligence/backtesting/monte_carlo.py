"""Monte Carlo: how bad could the PATH have been, given the same edge?

The point is NOT to predict the next trade. It is to answer questions the
headline metrics cannot:

    how deep a drawdown should I expect from this edge?
    how long a losing streak is normal rather than broken?
    what is the risk of ruin at this position size?

Two resampling schemes, and the difference matters:

    IID     shuffle trades independently -- assumes no serial dependence
    BLOCK   resample contiguous blocks -- keeps clusters of losses together,
            which is what actually kills accounts

If the block distribution is much worse than the IID one, the trade sequence
has dependence and the IID answer is too comfortable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import pandas as pd


class Resample(str, Enum):
    IID = "IID"
    BLOCK = "BLOCK"


@dataclass
class MonteCarloResult:
    method: Resample
    iterations: int
    trades_per_path: int
    final_r: np.ndarray = field(default_factory=lambda: np.empty(0))
    max_drawdown_r: np.ndarray = field(default_factory=lambda: np.empty(0))
    longest_losing_streak: np.ndarray = field(default_factory=lambda: np.empty(0))
    risk_of_ruin: float = float("nan")
    ruin_threshold: float = float("nan")

    def percentiles(self, values: np.ndarray,
                    points: tuple[float, ...] = (5, 25, 50, 75, 95)) -> dict[str, float]:
        if not len(values):
            return {}
        return {f"p{int(p)}": float(np.percentile(values, p)) for p in points}

    def summary(self) -> str:
        final = self.percentiles(self.final_r)
        drawdown = self.percentiles(self.max_drawdown_r)
        streak = self.percentiles(self.longest_losing_streak)
        return "\n".join([
            f"method            : {self.method.value}  "
            f"({self.iterations:,} paths x {self.trades_per_path} trades)",
            f"final R           : p5 {final.get('p5', float('nan')):.1f} | "
            f"median {final.get('p50', float('nan')):.1f} | "
            f"p95 {final.get('p95', float('nan')):.1f}",
            f"max drawdown R    : median {drawdown.get('p50', float('nan')):.1f} | "
            f"p95 {drawdown.get('p95', float('nan')):.1f}",
            f"losing streak     : median {streak.get('p50', float('nan')):.0f} | "
            f"p95 {streak.get('p95', float('nan')):.0f}",
            f"risk of ruin      : {self.risk_of_ruin:.1%} "
            f"(equity below {self.ruin_threshold:.0%} of start)",
        ])

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame({
            "final_r": self.final_r,
            "max_drawdown_r": self.max_drawdown_r,
            "longest_losing_streak": self.longest_losing_streak,
        })


def _longest_losing_streak(r: np.ndarray) -> int:
    longest = current = 0
    for value in r:
        current = current + 1 if value <= 0 else 0
        longest = max(longest, current)
    return longest


def _max_drawdown(curve: np.ndarray) -> float:
    peaks = np.maximum.accumulate(curve)
    return float(np.max(peaks - curve)) if len(curve) else float("nan")


def run_monte_carlo(
    r_multiples: list[float] | np.ndarray,
    *,
    method: Resample = Resample.BLOCK,
    iterations: int = 2000,
    trades_per_path: int | None = None,
    block_length: int = 5,
    risk_per_trade: float = 0.01,
    ruin_fraction: float = 0.5,
    seed: int = 20240819,
) -> MonteCarloResult:
    """Resample the realised R outcomes into many alternative histories."""
    r = np.asarray([x for x in r_multiples if x is not None and np.isfinite(x)],
                   dtype="float64")
    n = trades_per_path or len(r)
    result = MonteCarloResult(method=method, iterations=iterations, trades_per_path=n,
                              ruin_threshold=ruin_fraction)
    if len(r) < 2 or n < 1:
        return result

    rng = np.random.default_rng(seed)
    finals = np.empty(iterations)
    drawdowns = np.empty(iterations)
    streaks = np.empty(iterations)
    ruined = 0

    block = max(1, min(block_length, len(r)))
    starts_available = len(r) - block + 1
    draws = int(np.ceil(n / block))

    for i in range(iterations):
        if method is Resample.IID:
            path = rng.choice(r, size=n, replace=True)
        else:
            starts = rng.integers(0, starts_available, size=draws)
            path = np.concatenate([r[s:s + block] for s in starts])[:n]

        curve = np.concatenate([[0.0], np.cumsum(path)])
        finals[i] = curve[-1]
        drawdowns[i] = _max_drawdown(curve)
        streaks[i] = _longest_losing_streak(path)

        # Ruin is checked on a compounding account, where a run of losses
        # shrinks the stake as well as the balance.
        balance = 1.0
        for value in path:
            balance += balance * risk_per_trade * value
            if balance <= ruin_fraction:
                ruined += 1
                break

    result.final_r = finals
    result.max_drawdown_r = drawdowns
    result.longest_losing_streak = streaks
    result.risk_of_ruin = float(ruined / iterations)
    return result


def compare_methods(r_multiples: list[float], **kwargs) -> dict[str, MonteCarloResult]:
    """Run both schemes; a big gap between them means serial dependence."""
    return {
        Resample.IID.value: run_monte_carlo(r_multiples, method=Resample.IID, **kwargs),
        Resample.BLOCK.value: run_monte_carlo(r_multiples, method=Resample.BLOCK, **kwargs),
    }
