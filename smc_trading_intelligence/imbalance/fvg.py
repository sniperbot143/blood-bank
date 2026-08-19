"""Fair Value Gaps -- the three-bar imbalance, with a full lifecycle.

    bullish FVG   low[i]  > high[i-2]      gap = [high[i-2], low[i]]
    bearish FVG   high[i] < low[i-2]       gap = [high[i], low[i-2]]

The gap is knowable at bar `i` (the third bar), which is its
`confirmed_at_index`. From there it is tracked forward: how deep price has
traded back into it, whether it reached consequent encroachment (the midpoint),
and whether it was fully filled.

Fill depth only ever increases -- `fill_at(t)` reports the deepest penetration
up to bar `t`, so the state at any past bar is reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import pandas as pd

from common.indicators import wilder_atr
from config.smc_rules import DEFAULT_RULES, FVGConfig, SMCRules


class FVGDirection(str, Enum):
    BULLISH = "BULLISH"     # a gap below price that should support it
    BEARISH = "BEARISH"


class FVGStatus(str, Enum):
    FRESH = "FRESH"                 # untouched
    PARTIAL = "PARTIAL"             # traded into, not yet to CE
    MITIGATED = "MITIGATED"         # filled past the mitigation threshold (default CE)
    INVALIDATED = "INVALIDATED"     # fully filled


@dataclass
class FairValueGap:
    """One imbalance, with the bar-by-bar record of how it was filled."""

    direction: FVGDirection
    top: float
    bottom: float
    formed_at_index: int            # the third bar of the pattern
    confirmed_at_index: int         # same bar: the gap is visible as it closes
    formed_at: pd.Timestamp
    size: float
    size_atr: float
    displacement_score: float = float("nan")
    first_touch_index: int | None = None
    mitigated_at_index: int | None = None
    invalidated_at_index: int | None = None
    _fill_by_bar: np.ndarray | None = None   # deepest fill fraction up to each bar
    symbol: str = ""
    timeframe: str = ""

    @property
    def mid(self) -> float:
        """Consequent encroachment: the 50% level traders defend."""
        return (self.top + self.bottom) / 2.0

    def is_known_at(self, index: int) -> bool:
        return self.confirmed_at_index <= index

    def fill_at(self, index: int) -> float:
        """Deepest fill fraction (0..1) reached by bar `index`."""
        if self._fill_by_bar is None or not self.is_known_at(index):
            return 0.0
        i = min(index, len(self._fill_by_bar) - 1)
        return float(self._fill_by_bar[i])

    def status_at(self, index: int) -> FVGStatus:
        if self.invalidated_at_index is not None and index >= self.invalidated_at_index:
            return FVGStatus.INVALIDATED
        if self.mitigated_at_index is not None and index >= self.mitigated_at_index:
            return FVGStatus.MITIGATED
        if self.first_touch_index is not None and index >= self.first_touch_index:
            return FVGStatus.PARTIAL
        return FVGStatus.FRESH

    def is_active_at(self, index: int) -> bool:
        """Still tradeable: known, and not yet fully filled."""
        return self.is_known_at(index) and self.status_at(index) is not FVGStatus.INVALIDATED

    def age_at(self, index: int) -> int:
        return max(0, index - self.confirmed_at_index)

    def contains(self, price: float) -> bool:
        return self.bottom <= price <= self.top

    def as_dict(self, index: int) -> dict:
        return {
            "direction": self.direction.value,
            "top": self.top,
            "bottom": self.bottom,
            "mid": self.mid,
            "size_atr": self.size_atr,
            "status": self.status_at(index).value,
            "fill": self.fill_at(index),
            "age": self.age_at(index),
            "formed_at": self.formed_at,
            "displacement_score": self.displacement_score,
        }


@dataclass
class FVGSeries:
    gaps: list[FairValueGap] = field(default_factory=list)
    n_bars: int = 0
    symbol: str = ""
    timeframe: str = ""
    config: FVGConfig = field(default_factory=FVGConfig)
    rejected_small: int = 0

    def known_at(self, index: int) -> list[FairValueGap]:
        return [g for g in self.gaps if g.is_known_at(index)]

    def active_at(self, index: int) -> list[FairValueGap]:
        return [g for g in self.gaps if g.is_active_at(index)]

    def fresh_at(self, index: int) -> list[FairValueGap]:
        return [g for g in self.gaps
                if g.is_known_at(index) and g.status_at(index) is FVGStatus.FRESH]

    def nearest(self, price: float, index: int,
                direction: FVGDirection | None = None) -> FairValueGap | None:
        candidates = [g for g in self.active_at(index)
                      if direction is None or g.direction is direction]
        if not candidates:
            return None
        return min(candidates, key=lambda g: abs(g.mid - price))

    def leaves_imbalance_at(self, index: int) -> float:
        """1.0 if a gap formed on this bar -- the displacement imbalance input."""
        return 1.0 if any(g.confirmed_at_index == index for g in self.gaps) else 0.0

    def counts(self, index: int | None = None) -> dict[str, int]:
        at = self.n_bars - 1 if index is None else index
        counts: dict[str, int] = {}
        for gap in self.known_at(at):
            key = f"{gap.direction.value}|{gap.status_at(at).value}"
            counts[key] = counts.get(key, 0) + 1
        return counts

    def to_frame(self, index: int | None = None) -> pd.DataFrame:
        at = self.n_bars - 1 if index is None else index
        rows = [g.as_dict(at) for g in self.known_at(at)]
        return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["direction", "top", "bottom"])


def detect_fvgs(
    frame: pd.DataFrame,
    rules: SMCRules = DEFAULT_RULES,
    *,
    atr: pd.Series | None = None,
) -> FVGSeries:
    """Find every three-bar imbalance and track how each one fills."""
    config = rules.fvg
    n = len(frame)
    symbol = str(frame["symbol"].iloc[0]) if n and "symbol" in frame else ""
    timeframe = str(frame["timeframe"].iloc[0]) if n and "timeframe" in frame else ""
    series = FVGSeries(n_bars=n, symbol=symbol, timeframe=timeframe, config=config)
    if n < 3:
        return series

    highs = frame["high"].to_numpy("float64")
    lows = frame["low"].to_numpy("float64")
    atr_values = (atr if atr is not None else wilder_atr(frame, rules.atr_period)).to_numpy("float64")

    from structure.displacement import displacement_at

    for i in range(2, n):
        for direction, bottom, top in (
            (FVGDirection.BULLISH, highs[i - 2], lows[i]),
            (FVGDirection.BEARISH, highs[i], lows[i - 2]),
        ):
            if not (top > bottom):
                continue
            size = float(top - bottom)
            atr_i = atr_values[i]
            if not np.isfinite(atr_i) or atr_i <= 0:
                continue
            size_atr = size / atr_i
            if size_atr < config.min_size_atr:
                series.rejected_small += 1
                continue

            # The middle bar is the one that displaced; score it for context
            # (and, when required, as a filter).
            middle = displacement_at(
                frame, i - 1, bullish=direction is FVGDirection.BULLISH,
                atr_value=atr_values[i - 1], config=rules.displacement,
            )
            if config.require_displacement and not (
                np.isfinite(middle.score) and middle.score >= config.min_displacement
            ):
                continue

            gap = FairValueGap(
                direction=direction, top=float(top), bottom=float(bottom),
                formed_at_index=i, confirmed_at_index=i, formed_at=frame.index[i],
                size=size, size_atr=float(size_atr),
                displacement_score=float(middle.score),
                symbol=symbol, timeframe=timeframe,
            )
            _track_fill(gap, highs, lows, config)
            series.gaps.append(gap)

    return series


def _track_fill(gap: FairValueGap, highs: np.ndarray, lows: np.ndarray,
                config: FVGConfig) -> None:
    """Record the deepest fill reached by each bar after the gap forms."""
    n = len(highs)
    fills = np.zeros(n, dtype="float64")
    start = gap.confirmed_at_index + 1
    if start >= n or gap.size <= 0:
        gap._fill_by_bar = fills
        return

    if gap.direction is FVGDirection.BULLISH:
        # Price comes DOWN into a bullish gap from above.
        penetration = (gap.top - lows[start:]) / gap.size
    else:
        penetration = (highs[start:] - gap.bottom) / gap.size

    running = np.maximum.accumulate(np.clip(penetration, 0.0, None))
    fills[start:] = np.clip(running, 0.0, 1.0)
    gap._fill_by_bar = fills

    touched = np.flatnonzero(fills[start:] > 0)
    if touched.size:
        gap.first_touch_index = int(touched[0]) + start
    mitigated = np.flatnonzero(fills[start:] >= config.mitigated_fill)
    if mitigated.size:
        gap.mitigated_at_index = int(mitigated[0]) + start
    invalidated = np.flatnonzero(fills[start:] >= 1.0)
    if invalidated.size:
        gap.invalidated_at_index = int(invalidated[0]) + start
