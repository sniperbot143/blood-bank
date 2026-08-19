"""Liquidity sweeps: price reaches resting stops and is rejected.

Three things must all be true (docs/SMC_DEFINITIONS.md §8):

    1. liquidity exists   -- an INTACT pool from Phase 5
    2. price trades through it, by more than a minimum penetration
    3. price rejects      -- closes back on the origin side within a few bars

Two out of three is just a level being broken, which is the common case: 95%
of pools eventually end CONSUMED. The sweep is the *failed* break.

`confirmed_at_index` is the rejection bar, not the penetration bar -- at the
moment of penetration nobody knows whether it is a sweep or a breakout.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import pandas as pd

from common.indicators import wilder_atr
from config.smc_rules import DEFAULT_RULES, SMCRules, SweepConfig
from liquidity.levels import LiquidityMap, LiquidityPool, PoolKind, Side, build_liquidity
from liquidity.sessions import SessionSeries
from structure.market_structure import MarketStructure


class SweepType(str, Enum):
    BUY_SIDE_SWEEP = "BUY_SIDE_SWEEP"     # stops above highs taken, price rejected down
    SELL_SIDE_SWEEP = "SELL_SIDE_SWEEP"   # stops below lows taken, price rejected up

    @property
    def reaction_is_bearish(self) -> bool:
        return self is SweepType.BUY_SIDE_SWEEP


@dataclass
class SweepEvent:
    """One completed sweep, with everything Phase 12 needs as features."""

    type: SweepType
    pool_kind: PoolKind
    level: float
    penetration_index: int          # the bar that traded through
    confirmed_at_index: int         # the bar that rejected -- when this is KNOWN
    penetration_time: pd.Timestamp
    confirmed_at: pd.Timestamp
    extreme: float                  # the wick high/low of the penetration bar

    magnitude_atr: float = float("nan")        # how far past the level price went
    rejection_atr: float = float("nan")        # how far it came back by the reject bar
    close_location: float = float("nan")       # of the penetration bar
    bars_to_reject: int = 0
    pool_strength: float = float("nan")
    pool_touches: int = 0
    volume_ratio: float = float("nan")         # tick volume vs its own recent mean
    distance_from_structure_atr: float = float("nan")
    atr: float = float("nan")
    session: str = ""
    symbol: str = ""
    timeframe: str = ""

    @property
    def direction_bullish(self) -> bool:
        """The reaction direction: a sell-side sweep is a bullish signal."""
        return self.type is SweepType.SELL_SIDE_SWEEP

    def as_dict(self) -> dict:
        return {
            "type": self.type.value,
            "pool_kind": self.pool_kind.value,
            "level": self.level,
            "penetration_index": self.penetration_index,
            "confirmed_at_index": self.confirmed_at_index,
            "confirmed_at": self.confirmed_at,
            "magnitude_atr": self.magnitude_atr,
            "rejection_atr": self.rejection_atr,
            "close_location": self.close_location,
            "bars_to_reject": self.bars_to_reject,
            "pool_strength": self.pool_strength,
            "volume_ratio": self.volume_ratio,
            "session": self.session,
        }


@dataclass
class SweepSeries:
    events: list[SweepEvent] = field(default_factory=list)
    rejected_breakouts: int = 0        # penetrations too deep to be a sweep
    n_bars: int = 0
    symbol: str = ""
    timeframe: str = ""
    config: SweepConfig = field(default_factory=SweepConfig)

    def known_at(self, index: int) -> list[SweepEvent]:
        return [e for e in self.events if e.confirmed_at_index <= index]

    _last_any: list | None = None

    def last(self, index: int | None = None, type_: SweepType | None = None) -> SweepEvent | None:
        at = self.n_bars - 1 if index is None else index
        if type_ is None:
            if self._last_any is None:
                cache: list = [None] * max(self.n_bars, 1)
                current = None
                cursor = 0
                ordered = sorted(self.events, key=lambda e: e.confirmed_at_index)
                for t in range(self.n_bars):
                    while cursor < len(ordered) and ordered[cursor].confirmed_at_index <= t:
                        current = ordered[cursor]
                        cursor += 1
                    cache[t] = current
                self._last_any = cache
            if not self.n_bars:
                return None
            return self._last_any[max(0, min(at, self.n_bars - 1))]

        for event in reversed(self.events):
            if event.confirmed_at_index <= at and event.type is type_:
                return event
        return None

    def recent(self, index: int, lookback: int, type_: SweepType | None = None) -> list[SweepEvent]:
        """Sweeps confirmed within `lookback` bars of `index` -- the MSS origin test."""
        return [
            e for e in self.events
            if index - lookback <= e.confirmed_at_index <= index
            and (type_ is None or e.type is type_)
        ]

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for event in self.events:
            key = f"{event.type.value}|{event.pool_kind.value}"
            counts[key] = counts.get(key, 0) + 1
        return counts

    def type_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for event in self.events:
            counts[event.type.value] = counts.get(event.type.value, 0) + 1
        return counts

    def to_frame(self) -> pd.DataFrame:
        rows = [e.as_dict() for e in self.events]
        return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["type", "level"])


def detect_sweeps(
    frame: pd.DataFrame,
    liquidity: LiquidityMap | None = None,
    rules: SMCRules = DEFAULT_RULES,
    *,
    structure: MarketStructure | None = None,
    atr: pd.Series | None = None,
) -> SweepSeries:
    """Turn pool penetrations into sweep events with measured features."""
    config = rules.sweeps
    n = len(frame)
    liquidity = liquidity if liquidity is not None else build_liquidity(frame, rules, atr=atr)
    series = SweepSeries(n_bars=n, symbol=liquidity.symbol,
                         timeframe=liquidity.timeframe, config=config)
    if n == 0:
        return series

    atr_values = (atr if atr is not None else wilder_atr(frame, rules.atr_period)).to_numpy("float64")
    highs = frame["high"].to_numpy("float64")
    lows = frame["low"].to_numpy("float64")
    closes = frame["close"].to_numpy("float64")
    volume = (frame["tick_volume"].to_numpy("float64")
              if "tick_volume" in frame.columns else np.zeros(n))
    volume_mean = pd.Series(volume).rolling(20, min_periods=5).mean().to_numpy("float64")
    sessions: SessionSeries = liquidity.sessions

    for pool in liquidity.pools:
        event = _sweep_for_pool(
            pool, config, frame, highs, lows, closes, atr_values, volume, volume_mean,
            sessions, structure,
        )
        if event is None:
            continue
        if event == "BREAKOUT":
            series.rejected_breakouts += 1
            continue
        series.events.append(event)

    series.events.sort(key=lambda e: (e.confirmed_at_index, e.penetration_index))
    return series


def _sweep_for_pool(
    pool: LiquidityPool, config: SweepConfig, frame: pd.DataFrame,
    highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, atr_values: np.ndarray,
    volume: np.ndarray, volume_mean: np.ndarray, sessions: SessionSeries,
    structure: MarketStructure | None,
):
    """The first penetration of one pool, classified as a sweep or a breakout."""
    n = len(highs)
    start = pool.confirmed_at_index
    if start >= n:
        return None
    if pool.strength_at(start) < config.min_pool_strength:
        return None

    level = pool.price
    atr = pool.atr_at_creation
    if not np.isfinite(atr) or atr <= 0:
        return None

    min_pen = config.min_penetration_atr * atr
    max_pen = config.max_penetration_atr * atr
    above = pool.side is Side.BUY_SIDE

    # First bar that genuinely trades through the level.
    if above:
        candidates = np.flatnonzero(highs[start:] > level + min_pen)
    else:
        candidates = np.flatnonzero(lows[start:] < level - min_pen)
    if candidates.size == 0:
        return None

    i = int(candidates[0]) + start
    extreme = float(highs[i] if above else lows[i])
    magnitude = abs(extreme - level)

    # Too deep to be a sweep -- that is a breakout, and a different animal.
    if magnitude > max_pen:
        return "BREAKOUT"

    span = float(highs[i] - lows[i])
    close_location = ((highs[i] - closes[i]) / span if above else (closes[i] - lows[i]) / span) \
        if span > 0 else 0.0
    # `close_location` here measures rejection: 1.0 = closed at the far end
    # from the swept side. The definition's "close location <= 0.40" is stated
    # from the sweep side, so we require the mirror: >= 1 - max_close_location.
    if close_location < 1.0 - config.max_close_location:
        return None

    # The reclaim: a close back on the origin side within confirm_bars.
    reject_at = None
    for j in range(i, min(i + config.confirm_bars + 1, n)):
        back_inside = closes[j] < level if above else closes[j] > level
        if back_inside or not config.require_close_back:
            reject_at = j
            break
    if reject_at is None:
        return None

    atr_now = atr_values[reject_at]
    scale = atr_now if np.isfinite(atr_now) and atr_now > 0 else atr
    rejection = abs(extreme - closes[reject_at])

    vol_ratio = float("nan")
    if np.isfinite(volume_mean[i]) and volume_mean[i] > 0:
        vol_ratio = float(volume[i] / volume_mean[i])

    distance_structure = float("nan")
    if structure is not None:
        state = structure.state_at(i)
        reference = state.structural_high if above else state.structural_low
        if reference is not None and scale > 0:
            distance_structure = abs(level - reference.price) / scale

    return SweepEvent(
        type=SweepType.BUY_SIDE_SWEEP if above else SweepType.SELL_SIDE_SWEEP,
        pool_kind=pool.kind,
        level=level,
        penetration_index=i,
        confirmed_at_index=reject_at,
        penetration_time=frame.index[i],
        confirmed_at=frame.index[reject_at],
        extreme=extreme,
        magnitude_atr=float(magnitude / scale) if scale > 0 else float("nan"),
        rejection_atr=float(rejection / scale) if scale > 0 else float("nan"),
        close_location=float(close_location),
        bars_to_reject=reject_at - i,
        pool_strength=pool.strength_at(reject_at),
        pool_touches=pool.touch_count_at(reject_at),
        volume_ratio=vol_ratio,
        distance_from_structure_atr=distance_structure,
        atr=float(scale),
        session=sessions.session_at(i),
        symbol=pool.symbol,
        timeframe=pool.timeframe,
    )
