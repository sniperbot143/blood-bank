"""Order blocks: the last opposite candle before the move that broke structure.

    bullish OB   the last DOWN-close bar before a bullish displacement leg
                 that produced a BOS or MSS
    bearish OB   the last UP-close bar before a bearish one

Validity requires all of: a break event to anchor to, displacement on the leg,
a zone at least `min_size_atr` wide, and the block not already mitigated when
found.

Lifecycle: FRESH -> TOUCHED -> MITIGATED -> INVALIDATED -> BREAKER, all
recorded as indices so the state at any past bar is reproducible.

Structural note: `breakers.py` and `mitigation.py` were planned as separate
modules; both are one-line consequences of this lifecycle, so they live here
rather than as two files that only reach back into this one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import pandas as pd

from common.indicators import wilder_atr
from config.smc_rules import DEFAULT_RULES, OBZone, OrderBlockConfig, SMCRules
from structure.breaks import BreakSeries, BreakType, Direction, detect_breaks
from structure.market_structure import MarketStructure, build_structure


class OBDirection(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"


class OBStatus(str, Enum):
    FRESH = "FRESH"
    TOUCHED = "TOUCHED"
    MITIGATED = "MITIGATED"
    INVALIDATED = "INVALIDATED"
    BREAKER = "BREAKER"


@dataclass
class OrderBlock:
    """One order block with its zone, its anchor break and its lifecycle."""

    direction: OBDirection
    top: float
    bottom: float
    origin_index: int               # the bar the block sits on
    confirmed_at_index: int         # the break bar -- when the OB is KNOWN
    origin_time: pd.Timestamp
    confirmed_at: pd.Timestamp
    break_type: BreakType
    displacement_score: float = float("nan")
    size_atr: float = float("nan")
    atr_at_origin: float = float("nan")

    first_touch_index: int | None = None
    mitigated_at_index: int | None = None
    invalidated_at_index: int | None = None
    breaker_at_index: int | None = None
    _fill_by_bar: np.ndarray | None = None
    symbol: str = ""
    timeframe: str = ""

    @property
    def mid(self) -> float:
        return (self.top + self.bottom) / 2.0

    @property
    def size(self) -> float:
        return self.top - self.bottom

    def is_known_at(self, index: int) -> bool:
        return self.confirmed_at_index <= index

    def fill_at(self, index: int) -> float:
        if self._fill_by_bar is None or not self.is_known_at(index):
            return 0.0
        return float(self._fill_by_bar[min(index, len(self._fill_by_bar) - 1)])

    def status_at(self, index: int) -> OBStatus:
        if self.breaker_at_index is not None and index >= self.breaker_at_index:
            return OBStatus.BREAKER
        if self.invalidated_at_index is not None and index >= self.invalidated_at_index:
            return OBStatus.INVALIDATED
        if self.mitigated_at_index is not None and index >= self.mitigated_at_index:
            return OBStatus.MITIGATED
        if self.first_touch_index is not None and index >= self.first_touch_index:
            return OBStatus.TOUCHED
        return OBStatus.FRESH

    def is_fresh_at(self, index: int) -> bool:
        return self.is_known_at(index) and self.status_at(index) is OBStatus.FRESH

    def is_tradeable_at(self, index: int) -> bool:
        """Still usable as a point of interest in its original direction."""
        return self.is_known_at(index) and self.status_at(index) in (
            OBStatus.FRESH, OBStatus.TOUCHED)

    def breaker_direction(self) -> OBDirection:
        """A failed bullish OB becomes bearish resistance, and vice versa."""
        return (OBDirection.BEARISH if self.direction is OBDirection.BULLISH
                else OBDirection.BULLISH)

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
            "break_type": self.break_type.value,
            "displacement_score": self.displacement_score,
            "origin_time": self.origin_time,
        }


@dataclass
class OrderBlockSeries:
    blocks: list[OrderBlock] = field(default_factory=list)
    n_bars: int = 0
    symbol: str = ""
    timeframe: str = ""
    config: OrderBlockConfig = field(default_factory=OrderBlockConfig)
    rejected: dict[str, int] = field(default_factory=dict)

    def known_at(self, index: int) -> list[OrderBlock]:
        return [b for b in self.blocks if b.is_known_at(index)]

    def fresh_at(self, index: int) -> list[OrderBlock]:
        return [b for b in self.blocks if b.is_fresh_at(index)]

    def tradeable_at(self, index: int) -> list[OrderBlock]:
        return [b for b in self.blocks if b.is_tradeable_at(index)]

    def breakers_at(self, index: int) -> list[OrderBlock]:
        return [b for b in self.blocks
                if b.is_known_at(index) and b.status_at(index) is OBStatus.BREAKER]

    def nearest(self, price: float, index: int,
                direction: OBDirection | None = None) -> OrderBlock | None:
        candidates = [b for b in self.tradeable_at(index)
                      if direction is None or b.direction is direction]
        if not candidates:
            return None
        return min(candidates, key=lambda b: abs(b.mid - price))

    def counts(self, index: int | None = None) -> dict[str, int]:
        at = self.n_bars - 1 if index is None else index
        counts: dict[str, int] = {}
        for block in self.known_at(at):
            key = f"{block.direction.value}|{block.status_at(at).value}"
            counts[key] = counts.get(key, 0) + 1
        return counts

    def to_frame(self, index: int | None = None) -> pd.DataFrame:
        at = self.n_bars - 1 if index is None else index
        rows = [b.as_dict(at) for b in self.known_at(at)]
        return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["direction", "top", "bottom"])


def _zone(frame: pd.DataFrame, index: int, direction: OBDirection,
          config: OrderBlockConfig) -> tuple[float, float]:
    row = frame.iloc[index]
    high, low = float(row["high"]), float(row["low"])
    body_top = max(float(row["open"]), float(row["close"]))
    body_bottom = min(float(row["open"]), float(row["close"]))

    if config.zone is OBZone.BODY:
        return body_top, body_bottom
    if config.zone is OBZone.WICK_TO_BODY:
        return (body_top, low) if direction is OBDirection.BULLISH else (high, body_bottom)
    return high, low


def detect_order_blocks(
    frame: pd.DataFrame,
    breaks: BreakSeries | None = None,
    rules: SMCRules = DEFAULT_RULES,
    *,
    structure: MarketStructure | None = None,
    atr: pd.Series | None = None,
) -> OrderBlockSeries:
    """Find the origin candle of every structure-breaking leg."""
    config = rules.order_blocks
    n = len(frame)
    symbol = str(frame["symbol"].iloc[0]) if n and "symbol" in frame else ""
    timeframe = str(frame["timeframe"].iloc[0]) if n and "timeframe" in frame else ""
    series = OrderBlockSeries(n_bars=n, symbol=symbol, timeframe=timeframe, config=config)
    if n == 0:
        return series

    atr_series = atr if atr is not None else wilder_atr(frame, rules.atr_period)
    if breaks is None:
        structure = structure if structure is not None else build_structure(frame, rules, atr=atr_series)
        breaks = detect_breaks(frame, structure, rules, atr=atr_series)

    opens = frame["open"].to_numpy("float64")
    highs = frame["high"].to_numpy("float64")
    lows = frame["low"].to_numpy("float64")
    closes = frame["close"].to_numpy("float64")
    atr_values = atr_series.to_numpy("float64")

    def reject(reason: str) -> None:
        series.rejected[reason] = series.rejected.get(reason, 0) + 1

    seen: set[tuple[int, str]] = set()

    for event in breaks.events:
        if event.type not in (BreakType.BOS, BreakType.MSS):
            continue
        if not (np.isfinite(event.displacement.score)
                and event.displacement.score >= config.min_displacement):
            reject("NO_DISPLACEMENT")
            continue

        bullish = event.direction is Direction.BULLISH
        direction = OBDirection.BULLISH if bullish else OBDirection.BEARISH

        # Walk back for the last candle that closed AGAINST the break.
        origin = None
        start = event.displacement.start_index if event.displacement.start_index is not None else event.index
        for k in range(start, max(-1, start - config.max_lookback - 1), -1):
            if k < 0:
                break
            against = closes[k] < opens[k] if bullish else closes[k] > opens[k]
            if against:
                origin = k
                break
        if origin is None:
            reject("NO_ORIGIN_CANDLE")
            continue

        key = (origin, direction.value)
        if key in seen:
            continue
        seen.add(key)

        top, bottom = _zone(frame, origin, direction, config)
        atr_origin = atr_values[origin]
        if not np.isfinite(atr_origin) or atr_origin <= 0:
            reject("NO_ATR")
            continue
        size_atr = (top - bottom) / atr_origin
        if size_atr < config.min_size_atr:
            reject("TOO_SMALL")
            continue

        block = OrderBlock(
            direction=direction, top=float(top), bottom=float(bottom),
            origin_index=origin, confirmed_at_index=event.index,
            origin_time=frame.index[origin], confirmed_at=event.timestamp,
            break_type=event.type, displacement_score=float(event.displacement.score),
            size_atr=float(size_atr), atr_at_origin=float(atr_origin),
            symbol=symbol, timeframe=timeframe,
        )
        _track_lifecycle(block, highs, lows, closes, config)
        if block.mitigated_at_index is not None and \
                block.mitigated_at_index <= block.confirmed_at_index:
            reject("ALREADY_MITIGATED")
            continue
        series.blocks.append(block)

    series.blocks.sort(key=lambda b: (b.confirmed_at_index, b.origin_index))
    return series


def _track_lifecycle(block: OrderBlock, highs: np.ndarray, lows: np.ndarray,
                     closes: np.ndarray, config: OrderBlockConfig) -> None:
    n = len(highs)
    fills = np.zeros(n, dtype="float64")
    start = block.confirmed_at_index + 1
    size = block.size
    if start >= n or size <= 0:
        block._fill_by_bar = fills
        return

    if block.direction is OBDirection.BULLISH:
        # Price returns DOWN into a bullish block.
        penetration = (block.top - lows[start:]) / size
        beyond_close = closes[start:] < block.bottom
    else:
        penetration = (highs[start:] - block.bottom) / size
        beyond_close = closes[start:] > block.top

    fills[start:] = np.clip(np.maximum.accumulate(np.clip(penetration, 0.0, None)), 0.0, 1.0)
    block._fill_by_bar = fills

    touched = np.flatnonzero(fills[start:] > 0)
    if touched.size:
        block.first_touch_index = int(touched[0]) + start
    mitigated = np.flatnonzero(fills[start:] >= config.mitigation_fill)
    if mitigated.size:
        block.mitigated_at_index = int(mitigated[0]) + start

    invalid = np.flatnonzero(beyond_close)
    if invalid.size:
        block.invalidated_at_index = int(invalid[0]) + start

    # Breaker: after failing, price comes back to the zone from the other side.
    if config.track_breakers and block.invalidated_at_index is not None:
        window_start = block.invalidated_at_index + 1
        window_end = min(window_start + config.breaker_retest_bars, n)
        for j in range(window_start, window_end):
            if block.direction is OBDirection.BULLISH:
                retested = highs[j] >= block.bottom and closes[j] < block.bottom
            else:
                retested = lows[j] <= block.top and closes[j] > block.top
            if retested:
                block.breaker_at_index = j
                break
