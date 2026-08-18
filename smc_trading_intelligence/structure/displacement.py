"""Displacement -- "did price actually move, or did it just drift there?"

A break of a level means something different when it happens on a wide-bodied
bar that closes on its extreme than when it happens on a doji. MSS requires
displacement; BOS optionally does. Everything is measured in ATR so the same
thresholds work on EURUSDm and BTCUSDm.

Phase 4 ships three of the four components from docs/SMC_DEFINITIONS.md §6.
The fourth (does the move leave an imbalance/FVG) needs Phase 8, and is wired
as a config weight of 0.0 today so switching it on later is an explicit,
hash-changing decision rather than a silent redefinition of "STRONG".
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd

from config.smc_rules import DisplacementConfig


class DisplacementClass(str, Enum):
    NONE = "NONE"
    WEAK = "WEAK"
    MODERATE = "MODERATE"
    STRONG = "STRONG"


@dataclass(frozen=True)
class Displacement:
    """The displacement reading for one bar, in one direction."""

    score: float
    grade: DisplacementClass
    body_atr: float
    range_atr: float
    close_location: float
    imbalance: float = 0.0
    atr: float = float("nan")

    @property
    def is_displacement(self) -> bool:
        return self.grade is not DisplacementClass.NONE

    def as_dict(self) -> dict:
        return {
            "displacement_score": self.score,
            "displacement_class": self.grade.value,
            "body_atr": self.body_atr,
            "range_atr": self.range_atr,
            "close_location": self.close_location,
        }


UNKNOWN = Displacement(
    score=float("nan"), grade=DisplacementClass.NONE,
    body_atr=float("nan"), range_atr=float("nan"), close_location=float("nan"),
)


def _clamp01(value: float) -> float:
    return float(min(1.0, max(0.0, value)))


def close_location(open_: float, high: float, low: float, close: float,
                   *, bullish: bool) -> float:
    """Where the close sits in the bar's range: 1.0 = on the extreme.

    A zero-range bar has no location; by convention it scores 0.0 (no thrust).
    """
    span = high - low
    if span <= 0:
        return 0.0
    return (close - low) / span if bullish else (high - close) / span


def displacement_at(
    frame: pd.DataFrame,
    index: int,
    *,
    bullish: bool,
    atr_value: float,
    config: DisplacementConfig,
    imbalance: float = 0.0,
) -> Displacement:
    """Score bar `index` as displacement in the given direction.

    Uses only that bar plus ATR (which is itself causal), so the result at bar
    i can never change later.
    """
    if not (0 <= index < len(frame)):
        raise IndexError(f"bar {index} out of range for {len(frame)} bars")
    if not np.isfinite(atr_value) or atr_value <= 0:
        return UNKNOWN

    row = frame.iloc[index]
    open_, high, low, close = (float(row["open"]), float(row["high"]),
                               float(row["low"]), float(row["close"]))

    # A bar that closes against the direction is not displacement in it.
    directional_body = (close - open_) if bullish else (open_ - close)
    body_atr = directional_body / atr_value
    range_atr = (high - low) / atr_value
    location = close_location(open_, high, low, close, bullish=bullish)

    body_component = _clamp01(body_atr / config.body_atr_full)
    range_component = _clamp01(range_atr / config.range_atr_full)
    location_component = _clamp01(
        (location - config.close_location_min) / (1.0 - config.close_location_min)
    )

    score = (
        config.body_weight * body_component
        + config.range_weight * range_component
        + config.close_weight * location_component
        + config.imbalance_weight * _clamp01(imbalance)
    )

    return Displacement(
        score=float(score),
        grade=classify(score, config),
        body_atr=float(body_atr),
        range_atr=float(range_atr),
        close_location=float(location),
        imbalance=float(imbalance),
        atr=float(atr_value),
    )


def classify(score: float, config: DisplacementConfig) -> DisplacementClass:
    if not np.isfinite(score) or score < config.weak_threshold:
        return DisplacementClass.NONE
    if score < config.moderate_threshold:
        return DisplacementClass.WEAK
    if score < config.strong_threshold:
        return DisplacementClass.MODERATE
    return DisplacementClass.STRONG
