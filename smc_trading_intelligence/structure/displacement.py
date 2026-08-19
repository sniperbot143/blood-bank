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
    bars: int = 1                 # how many bars the scored leg spans
    start_index: int | None = None
    end_index: int | None = None

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
        bars=1,
        start_index=index,
        end_index=index,
    )


def displacement_run_at(
    frame: pd.DataFrame,
    index: int,
    *,
    bullish: bool,
    atr_value: float,
    config: DisplacementConfig,
    imbalance: float = 0.0,
) -> Displacement:
    """Score the best same-direction RUN of bars ending at `index`.

    Three 0.4-ATR bars in a row are a displacement leg; scoring only the last
    one would miss it. Every candidate run ENDS at `index` and extends back at
    most `max_run_bars`, so no information after `index` is used. The best
    scoring run wins, and the single bar is always among the candidates.
    """
    if not np.isfinite(atr_value) or atr_value <= 0:
        return UNKNOWN

    best = displacement_at(frame, index, bullish=bullish, atr_value=atr_value,
                           config=config, imbalance=imbalance)
    if config.max_run_bars <= 1:
        return best

    opens = frame["open"].to_numpy(dtype="float64")
    highs = frame["high"].to_numpy(dtype="float64")
    lows = frame["low"].to_numpy(dtype="float64")
    closes = frame["close"].to_numpy(dtype="float64")

    for span in range(2, config.max_run_bars + 1):
        start = index - span + 1
        if start < 0:
            break
        if config.require_same_direction_run:
            bodies = closes[start:index + 1] - opens[start:index + 1]
            if bullish and not (bodies > 0).all():
                break
            if not bullish and not (bodies < 0).all():
                break

        run_open = float(opens[start])
        run_close = float(closes[index])
        run_high = float(highs[start:index + 1].max())
        run_low = float(lows[start:index + 1].min())

        directional_body = (run_close - run_open) if bullish else (run_open - run_close)
        body_atr = directional_body / atr_value
        range_atr = (run_high - run_low) / atr_value
        location = close_location(run_open, run_high, run_low, run_close, bullish=bullish)

        score = (
            config.body_weight * _clamp01(body_atr / config.body_atr_full)
            + config.range_weight * _clamp01(range_atr / config.range_atr_full)
            + config.close_weight * _clamp01(
                (location - config.close_location_min) / (1.0 - config.close_location_min))
            + config.imbalance_weight * _clamp01(imbalance)
        )
        if score > best.score:
            best = Displacement(
                score=float(score), grade=classify(score, config),
                body_atr=float(body_atr), range_atr=float(range_atr),
                close_location=float(location), imbalance=float(imbalance),
                atr=float(atr_value), bars=span, start_index=start, end_index=index,
            )
    return best


def classify(score: float, config: DisplacementConfig) -> DisplacementClass:
    if not np.isfinite(score) or score < config.weak_threshold:
        return DisplacementClass.NONE
    if score < config.moderate_threshold:
        return DisplacementClass.WEAK
    if score < config.strong_threshold:
        return DisplacementClass.MODERATE
    return DisplacementClass.STRONG
