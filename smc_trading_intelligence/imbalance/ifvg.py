"""Inverse Fair Value Gaps -- a gap that failed, then flipped polarity.

Exact rule (docs/SMC_DEFINITIONS.md §10), for a bullish FVG becoming a
bearish IFVG:

    1. a bar CLOSES fully below the gap's low  -- invalidation by close, not
       by wick; a wick through is just the gap being filled;
    2. within `ifvg_reclaim_bars`, price trades back INTO the original range
       and closes below it again -- the failed support now acting as
       resistance.

The IFVG occupies the original gap's range, carries `origin` back to it, and
is tracked with the same fill lifecycle. `confirmed_at_index` is the reclaim
bar, which is the first moment the inversion is knowable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from config.smc_rules import DEFAULT_RULES, FVGConfig, SMCRules
from imbalance.fvg import FairValueGap, FVGDirection, FVGSeries, FVGStatus, detect_fvgs


@dataclass
class InverseFVG:
    """A flipped gap. Its direction is the OPPOSITE of the gap it came from."""

    direction: FVGDirection
    top: float
    bottom: float
    origin_formed_index: int
    invalidated_at_index: int      # the close that killed the original gap
    confirmed_at_index: int        # the reclaim close -- when this is knowable
    confirmed_at: pd.Timestamp
    size_atr: float
    origin: FairValueGap | None = None
    respected_count: int = 0
    symbol: str = ""
    timeframe: str = ""

    @property
    def mid(self) -> float:
        return (self.top + self.bottom) / 2.0

    def is_known_at(self, index: int) -> bool:
        return self.confirmed_at_index <= index

    def contains(self, price: float) -> bool:
        return self.bottom <= price <= self.top

    def as_dict(self) -> dict:
        return {
            "direction": self.direction.value,
            "top": self.top,
            "bottom": self.bottom,
            "size_atr": self.size_atr,
            "confirmed_at": self.confirmed_at,
            "origin_formed_index": self.origin_formed_index,
            "respected_count": self.respected_count,
        }


@dataclass
class IFVGSeries:
    inversions: list[InverseFVG] = field(default_factory=list)
    n_bars: int = 0
    symbol: str = ""
    timeframe: str = ""
    config: FVGConfig = field(default_factory=FVGConfig)

    def known_at(self, index: int) -> list[InverseFVG]:
        return [i for i in self.inversions if i.is_known_at(index)]

    def nearest(self, price: float, index: int,
                direction: FVGDirection | None = None) -> InverseFVG | None:
        candidates = [i for i in self.known_at(index)
                      if direction is None or i.direction is direction]
        if not candidates:
            return None
        return min(candidates, key=lambda i: abs(i.mid - price))

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.inversions:
            counts[item.direction.value] = counts.get(item.direction.value, 0) + 1
        return counts

    def to_frame(self) -> pd.DataFrame:
        rows = [i.as_dict() for i in self.inversions]
        return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["direction", "top", "bottom"])


def detect_ifvgs(
    frame: pd.DataFrame,
    fvgs: FVGSeries | None = None,
    rules: SMCRules = DEFAULT_RULES,
    *,
    atr: pd.Series | None = None,
) -> IFVGSeries:
    """Find gaps that were closed through and then rejected from the far side."""
    config = rules.fvg
    n = len(frame)
    fvgs = fvgs if fvgs is not None else detect_fvgs(frame, rules, atr=atr)
    series = IFVGSeries(n_bars=n, symbol=fvgs.symbol, timeframe=fvgs.timeframe, config=config)
    if n == 0:
        return series

    highs = frame["high"].to_numpy("float64")
    lows = frame["low"].to_numpy("float64")
    closes = frame["close"].to_numpy("float64")

    for gap in fvgs.gaps:
        start = gap.confirmed_at_index + 1
        if start >= n:
            continue

        bullish = gap.direction is FVGDirection.BULLISH
        # 1. a CLOSE fully beyond the gap kills it.
        if bullish:
            killed = np.flatnonzero(closes[start:] < gap.bottom)
        else:
            killed = np.flatnonzero(closes[start:] > gap.top)
        if killed.size == 0:
            continue
        kill_index = int(killed[0]) + start

        # 2. a return into the range that closes back beyond it = inversion.
        window_end = min(kill_index + config.ifvg_reclaim_bars, n - 1)
        reclaim = None
        for j in range(kill_index + 1, window_end + 1):
            if bullish:
                re_entered = highs[j] >= gap.bottom
                rejected = closes[j] < gap.bottom
            else:
                re_entered = lows[j] <= gap.top
                rejected = closes[j] > gap.top
            if re_entered and rejected:
                reclaim = j
                break
        if reclaim is None:
            continue

        inverse = InverseFVG(
            direction=(FVGDirection.BEARISH if bullish else FVGDirection.BULLISH),
            top=gap.top, bottom=gap.bottom,
            origin_formed_index=gap.formed_at_index,
            invalidated_at_index=kill_index,
            confirmed_at_index=reclaim,
            confirmed_at=frame.index[reclaim],
            size_atr=gap.size_atr, origin=gap,
            symbol=gap.symbol, timeframe=gap.timeframe,
        )
        # How often the flipped level held afterwards -- a quality signal.
        after = slice(reclaim + 1, n)
        if bullish:
            inverse.respected_count = int(np.sum(
                (highs[after] >= gap.bottom) & (closes[after] < gap.bottom)))
        else:
            inverse.respected_count = int(np.sum(
                (lows[after] <= gap.top) & (closes[after] > gap.top)))
        series.inversions.append(inverse)

    series.inversions.sort(key=lambda i: i.confirmed_at_index)
    return series
