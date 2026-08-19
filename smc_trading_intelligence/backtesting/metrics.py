"""Performance metrics, in R and in account terms.

Everything is computed from a list of realised R multiples plus an equity
curve. Reported deliberately together, because each one alone misleads:

  * win rate without expectancy hides a 90%-win, 1:0.1 disaster
  * expectancy without drawdown hides an unsurvivable path
  * profit factor without sample size hides luck
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class Metrics:
    trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = float("nan")
    expectancy_r: float = float("nan")
    median_r: float = float("nan")
    total_r: float = 0.0
    profit_factor: float = float("nan")
    max_drawdown_r: float = float("nan")
    max_drawdown_pct: float = float("nan")
    sharpe: float = float("nan")
    sortino: float = float("nan")
    mean_mae_r: float = float("nan")
    mean_mfe_r: float = float("nan")
    mean_bars_held: float = float("nan")
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    final_equity: float = float("nan")
    ambiguous_share: float = float("nan")
    extra: dict = field(default_factory=dict)

    def summary(self) -> str:
        def fmt(value: float, spec: str = ".3f") -> str:
            return f"{value:{spec}}" if np.isfinite(value) else "n/a"

        return "\n".join([
            f"trades            : {self.trades:,}  ({self.wins} W / {self.losses} L)",
            f"win rate          : {fmt(self.win_rate, '.1%')}",
            f"expectancy        : {fmt(self.expectancy_r)} R per trade",
            f"total             : {fmt(self.total_r, '.1f')} R",
            f"profit factor     : {fmt(self.profit_factor, '.2f')}",
            f"max drawdown      : {fmt(self.max_drawdown_r, '.1f')} R "
            f"({fmt(self.max_drawdown_pct, '.1%')})",
            f"Sharpe / Sortino  : {fmt(self.sharpe, '.2f')} / {fmt(self.sortino, '.2f')}",
            f"MAE / MFE         : {fmt(self.mean_mae_r, '.2f')} / {fmt(self.mean_mfe_r, '.2f')} R",
            f"avg bars held     : {fmt(self.mean_bars_held, '.0f')}",
            f"worst streak      : {self.max_consecutive_losses} losses "
            f"(best {self.max_consecutive_wins} wins)",
            f"ambiguous fills   : {fmt(self.ambiguous_share, '.1%')}",
        ])

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k != "extra"} | self.extra


def _streaks(wins: np.ndarray) -> tuple[int, int]:
    best = worst = current_w = current_l = 0
    for win in wins:
        if win:
            current_w, current_l = current_w + 1, 0
        else:
            current_l, current_w = current_l + 1, 0
        best, worst = max(best, current_w), max(worst, current_l)
    return best, worst


def drawdown(equity: np.ndarray) -> tuple[float, float]:
    """Largest peak-to-trough fall, in units and as a fraction of the peak."""
    if not len(equity):
        return float("nan"), float("nan")
    peaks = np.maximum.accumulate(equity)
    falls = peaks - equity
    worst = float(falls.max())
    at = int(np.argmax(falls))
    peak = float(peaks[at])
    return worst, (worst / peak if peak > 0 else float("nan"))


def compute_metrics(
    r_multiples: list[float] | np.ndarray,
    *,
    mae: list[float] | None = None,
    mfe: list[float] | None = None,
    bars_held: list[float] | None = None,
    ambiguous: list[bool] | None = None,
    periods_per_year: float = 252.0,
) -> Metrics:
    """All the headline numbers from one list of R outcomes."""
    r = np.asarray([x for x in r_multiples if x is not None and np.isfinite(x)], dtype="float64")
    metrics = Metrics(trades=int(len(r)))
    if not len(r):
        return metrics

    wins_mask = r > 0
    metrics.wins = int(wins_mask.sum())
    metrics.losses = int((~wins_mask).sum())
    metrics.win_rate = float(wins_mask.mean())
    metrics.expectancy_r = float(r.mean())
    metrics.median_r = float(np.median(r))
    metrics.total_r = float(r.sum())

    gains = float(r[wins_mask].sum())
    pains = float(-r[~wins_mask].sum())
    metrics.profit_factor = float(gains / pains) if pains > 0 else float("inf")

    curve = np.concatenate([[0.0], np.cumsum(r)])
    metrics.final_equity = float(curve[-1])
    # Drawdown in R is meaningful; a PERCENTAGE of an R curve is not (the curve
    # can sit at or below zero), so it is left to the account-equity version.
    metrics.max_drawdown_r, _ = drawdown(curve)

    std = float(r.std(ddof=1)) if len(r) > 1 else 0.0
    if std > 0:
        metrics.sharpe = float(r.mean() / std * np.sqrt(periods_per_year))
    downside = r[r < 0]
    downside_std = float(downside.std(ddof=1)) if len(downside) > 1 else 0.0
    if downside_std > 0:
        metrics.sortino = float(r.mean() / downside_std * np.sqrt(periods_per_year))

    metrics.max_consecutive_wins, metrics.max_consecutive_losses = _streaks(wins_mask)

    if mae:
        values = np.asarray([x for x in mae if np.isfinite(x)], dtype="float64")
        metrics.mean_mae_r = float(values.mean()) if len(values) else float("nan")
    if mfe:
        values = np.asarray([x for x in mfe if np.isfinite(x)], dtype="float64")
        metrics.mean_mfe_r = float(values.mean()) if len(values) else float("nan")
    if bars_held:
        values = np.asarray([x for x in bars_held if np.isfinite(x)], dtype="float64")
        metrics.mean_bars_held = float(values.mean()) if len(values) else float("nan")
    if ambiguous:
        metrics.ambiguous_share = float(np.mean([bool(x) for x in ambiguous]))

    return metrics


def account_drawdown(equity: pd.Series) -> tuple[float, float]:
    """Drawdown of an ACCOUNT curve, where a percentage does mean something."""
    return drawdown(np.asarray(equity, dtype="float64"))


def equity_curve(r_multiples: list[float], *, risk_per_trade: float = 0.01,
                 starting_balance: float = 10_000.0, compounding: bool = True) -> pd.Series:
    """Account equity from a sequence of R outcomes."""
    balance = starting_balance
    values = [balance]
    for r in r_multiples:
        if not np.isfinite(r):
            continue
        stake = balance * risk_per_trade if compounding else starting_balance * risk_per_trade
        balance += stake * r
        values.append(balance)
    return pd.Series(values, name="equity")
