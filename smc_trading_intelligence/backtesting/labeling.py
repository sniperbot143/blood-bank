"""Outcome labelling: what actually happened to each candidate.

Rules that decide whether any of the later statistics mean anything
(docs/PROBABILITY_METHODOLOGY.md §2):

  * **Fill first.** A limit at the point of interest is only filled if price
    reaches it within `entry_valid_bars`; unfilled candidates are `NO_FILL`
    and are excluded from win rates (but kept, so fill rate is measurable).
  * **Intrabar ambiguity resolves against us.** If one bar's range covers both
    TP and SL, the label is `SL_FIRST` and `ambiguous` is set. Assuming the
    good side would manufacture edge out of ignorance.
  * **Costs are applied here, not later.** Spread and slippage come out of the
    fill and out of the exit, so a setup that only works gross is a loser in
    the database.
  * **No look-ahead.** Labelling walks forward from the signal bar only.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd

from config.smc_rules import SetupConfig
from signals.setups import SetupCandidate


class Outcome(str, Enum):
    TP1_FIRST = "TP1_FIRST"
    TP2_FIRST = "TP2_FIRST"
    TP3_FIRST = "TP3_FIRST"
    SL_FIRST = "SL_FIRST"
    TIMEOUT = "TIMEOUT"
    NO_FILL = "NO_FILL"
    OPEN = "OPEN"           # still unresolved at the end of the data

    @property
    def is_resolved(self) -> bool:
        return self not in (Outcome.OPEN, Outcome.NO_FILL)

    @property
    def hit_tp1(self) -> bool:
        return self in (Outcome.TP1_FIRST, Outcome.TP2_FIRST, Outcome.TP3_FIRST)


@dataclass
class LabelledOutcome:
    """The resolved result of one candidate, in R and in bars."""

    outcome: Outcome
    r_multiple: float = float("nan")
    fill_index: int | None = None
    resolved_index: int | None = None
    resolved_at: pd.Timestamp | None = None
    bars_to_result: int = 0
    mae_r: float = float("nan")          # worst excursion against, in R
    mfe_r: float = float("nan")          # best excursion for, in R
    ambiguous: bool = False
    cost_r: float = float("nan")

    @property
    def is_win(self) -> bool:
        return np.isfinite(self.r_multiple) and self.r_multiple > 0

    def as_dict(self) -> dict:
        return {
            "outcome": self.outcome.value,
            "r_multiple": self.r_multiple,
            "fill_index": self.fill_index,
            "resolved_index": self.resolved_index,
            "resolved_at": self.resolved_at,
            "bars_to_result": self.bars_to_result,
            "mae_r": self.mae_r,
            "mfe_r": self.mfe_r,
            "ambiguous": int(self.ambiguous),
            "cost_r": self.cost_r,
        }


def label_outcome(
    frame: pd.DataFrame,
    candidate: SetupCandidate,
    config: SetupConfig,
    *,
    atr: float | None = None,
) -> LabelledOutcome:
    """Walk forward from the signal bar and record what happened."""
    levels = candidate.levels
    if not levels.is_valid:
        return LabelledOutcome(outcome=Outcome.NO_FILL)

    n = len(frame)
    start = candidate.signal_index + 1        # the signal bar itself is not tradeable
    if start >= n:
        return LabelledOutcome(outcome=Outcome.OPEN)

    highs = frame["high"].to_numpy("float64")
    lows = frame["low"].to_numpy("float64")
    bullish = candidate.bullish
    atr_value = atr if atr is not None else candidate.features.get("atr")
    cost = 0.0
    if atr_value and np.isfinite(atr_value):
        cost = (config.spread_cost_atr + config.slippage_atr) * float(atr_value)

    # -- fill -------------------------------------------------------------
    fill_index = None
    limit = min(start + config.entry_valid_bars, n)
    for i in range(start, limit):
        reached = lows[i] <= levels.entry if bullish else highs[i] >= levels.entry
        if reached:
            fill_index = i
            break
    if fill_index is None:
        return LabelledOutcome(outcome=Outcome.NO_FILL)

    # Costs push the fill against us and shrink the reward on the way out.
    entry = levels.entry + cost if bullish else levels.entry - cost
    risk = abs(entry - levels.stop_loss)
    if risk <= 0:
        return LabelledOutcome(outcome=Outcome.NO_FILL)

    mae = 0.0
    mfe = 0.0
    end = min(fill_index + config.max_hold_bars, n - 1)

    for i in range(fill_index, end + 1):
        high, low = highs[i], lows[i]
        favourable = (high - entry) if bullish else (entry - low)
        adverse = (entry - low) if bullish else (high - entry)
        mfe = max(mfe, favourable / risk)
        mae = max(mae, adverse / risk)

        hit_sl = low <= levels.stop_loss if bullish else high >= levels.stop_loss
        hit_tp1 = high >= levels.take_profit_1 if bullish else low <= levels.take_profit_1
        hit_tp2 = high >= levels.take_profit_2 if bullish else low <= levels.take_profit_2
        hit_tp3 = high >= levels.take_profit_3 if bullish else low <= levels.take_profit_3

        if hit_sl and hit_tp1:
            # Both inside one bar: we cannot know the order, so assume the worst.
            return LabelledOutcome(
                outcome=Outcome.SL_FIRST, r_multiple=-1.0, fill_index=fill_index,
                resolved_index=i, resolved_at=frame.index[i],
                bars_to_result=i - candidate.signal_index, mae_r=mae, mfe_r=mfe,
                ambiguous=True, cost_r=cost / risk,
            )
        if hit_sl:
            return LabelledOutcome(
                outcome=Outcome.SL_FIRST, r_multiple=-1.0, fill_index=fill_index,
                resolved_index=i, resolved_at=frame.index[i],
                bars_to_result=i - candidate.signal_index, mae_r=mae, mfe_r=mfe,
                cost_r=cost / risk,
            )
        if hit_tp1:
            if hit_tp3:
                outcome, exit_price = Outcome.TP3_FIRST, levels.take_profit_3
            elif hit_tp2:
                outcome, exit_price = Outcome.TP2_FIRST, levels.take_profit_2
            else:
                outcome, exit_price = Outcome.TP1_FIRST, levels.take_profit_1
            net = (exit_price - entry - cost) if bullish else (entry - exit_price - cost)
            return LabelledOutcome(
                outcome=outcome, r_multiple=float(net / risk), fill_index=fill_index,
                resolved_index=i, resolved_at=frame.index[i],
                bars_to_result=i - candidate.signal_index, mae_r=mae, mfe_r=mfe,
                cost_r=cost / risk,
            )

    if end >= n - 1 and (end - fill_index) < config.max_hold_bars:
        return LabelledOutcome(outcome=Outcome.OPEN, fill_index=fill_index,
                               mae_r=mae, mfe_r=mfe, cost_r=cost / risk)

    close = float(frame["close"].iloc[end])
    net = (close - entry - cost) if bullish else (entry - close - cost)
    return LabelledOutcome(
        outcome=Outcome.TIMEOUT, r_multiple=float(net / risk), fill_index=fill_index,
        resolved_index=end, resolved_at=frame.index[end],
        bars_to_result=end - candidate.signal_index, mae_r=mae, mfe_r=mfe,
        cost_r=cost / risk,
    )


def label_all(frame: pd.DataFrame, candidates: list[SetupCandidate],
              config: SetupConfig) -> list[LabelledOutcome]:
    return [label_outcome(frame, candidate, config) for candidate in candidates]


def outcome_counts(outcomes: list[LabelledOutcome]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in outcomes:
        counts[item.outcome.value] = counts.get(item.outcome.value, 0) + 1
    return counts
