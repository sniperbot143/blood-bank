"""Liquidity pools: the levels stops rest against, and their lifecycle.

Pool types (docs/SMC_DEFINITIONS.md §7): swing highs/lows, equal highs/lows,
previous day and week highs/lows, previous session highs/lows.

Side follows the convention that buy-side liquidity sits ABOVE price (buy stops
above old highs) and sell-side BELOW it. So a swing high is a BUY_SIDE pool.

Lifecycle, all as-of honest:

    INTACT    price has not traded beyond the level since the pool formed
    SWEPT     a wick traded beyond it but the bar closed back on the origin side
    CONSUMED  a bar closed beyond it -- the level is spent

Phase 5 records *that* a pool was swept. Phase 6 turns the sweep into an event
with magnitude, rejection strength and a reclaim test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import pandas as pd

from common.indicators import wilder_atr
from config.smc_rules import DEFAULT_RULES, LiquidityConfig, SMCRules
from liquidity.equal_levels import EqualLevelCluster, find_equal_levels
from liquidity.sessions import SessionSeries, build_sessions
from structure.swings import SwingKind, SwingSeries, detect_swings


class PoolKind(str, Enum):
    SWING_HIGH = "SWING_HIGH"
    SWING_LOW = "SWING_LOW"
    EQH = "EQH"
    EQL = "EQL"
    PDH = "PDH"
    PDL = "PDL"
    PWH = "PWH"
    PWL = "PWL"
    SESSION_HIGH = "SESSION_HIGH"
    SESSION_LOW = "SESSION_LOW"


class Side(str, Enum):
    BUY_SIDE = "BUY_SIDE"     # above price: buy stops resting over highs
    SELL_SIDE = "SELL_SIDE"   # below price: sell stops resting under lows

    @property
    def is_above(self) -> bool:
        return self is Side.BUY_SIDE


class PoolStatus(str, Enum):
    INTACT = "INTACT"
    SWEPT = "SWEPT"
    CONSUMED = "CONSUMED"


_SIDE_BY_KIND = {
    PoolKind.SWING_HIGH: Side.BUY_SIDE, PoolKind.EQH: Side.BUY_SIDE,
    PoolKind.PDH: Side.BUY_SIDE, PoolKind.PWH: Side.BUY_SIDE,
    PoolKind.SESSION_HIGH: Side.BUY_SIDE,
    PoolKind.SWING_LOW: Side.SELL_SIDE, PoolKind.EQL: Side.SELL_SIDE,
    PoolKind.PDL: Side.SELL_SIDE, PoolKind.PWL: Side.SELL_SIDE,
    PoolKind.SESSION_LOW: Side.SELL_SIDE,
}


@dataclass
class LiquidityPool:
    """One resting-liquidity level with a recorded lifecycle."""

    kind: PoolKind
    side: Side
    price: float
    created_at_index: int          # the bar whose price formed the level
    confirmed_at_index: int        # the first bar the pool could be KNOWN
    created_at: pd.Timestamp
    confirmed_at: pd.Timestamp
    origin: str = ""               # human-readable source ("LONDON 2024-03-14")
    cluster: EqualLevelCluster | None = None
    touch_indices: list[int] = field(default_factory=list)
    swept_at_index: int | None = None
    consumed_at_index: int | None = None
    atr_at_creation: float = float("nan")
    symbol: str = ""
    timeframe: str = ""
    _config: LiquidityConfig | None = None

    # -- as-of accessors ---------------------------------------------------

    def is_known_at(self, index: int) -> bool:
        return self.confirmed_at_index <= index

    def status_at(self, index: int) -> PoolStatus:
        if self.consumed_at_index is not None and index >= self.consumed_at_index:
            return PoolStatus.CONSUMED
        if self.swept_at_index is not None and index >= self.swept_at_index:
            return PoolStatus.SWEPT
        return PoolStatus.INTACT

    def is_intact_at(self, index: int) -> bool:
        return self.is_known_at(index) and self.status_at(index) is PoolStatus.INTACT

    def touch_count_at(self, index: int) -> int:
        return sum(1 for i in self.touch_indices if i <= index)

    def price_at(self, index: int) -> float:
        """Equal-level pools defend the extreme of the members known so far."""
        if self.cluster is not None:
            return self.cluster.price_at(index)
        return self.price

    def member_count_at(self, index: int) -> int:
        return self.cluster.member_count_at(index) if self.cluster is not None else 1

    def strength_at(self, index: int, config: LiquidityConfig | None = None) -> float:
        """How much liquidity should be expected here.

        base(kind) + per-touch increment (capped) + per-extra-member increment
        for equal levels, scaled by how tightly those members line up.
        """
        cfg = config or self._config or LiquidityConfig()
        base = {
            PoolKind.SWING_HIGH: cfg.strength_swing, PoolKind.SWING_LOW: cfg.strength_swing,
            PoolKind.EQH: cfg.strength_equal, PoolKind.EQL: cfg.strength_equal,
            PoolKind.PDH: cfg.strength_daily, PoolKind.PDL: cfg.strength_daily,
            PoolKind.PWH: cfg.strength_weekly, PoolKind.PWL: cfg.strength_weekly,
            PoolKind.SESSION_HIGH: cfg.strength_session,
            PoolKind.SESSION_LOW: cfg.strength_session,
        }[self.kind]

        touches = min(self.touch_count_at(index), cfg.strength_max_touches)
        strength = base + cfg.strength_per_touch * touches

        if self.cluster is not None:
            extra = max(0, self.cluster.member_count_at(index) - 2)
            strength += cfg.strength_per_extra_member * extra
            strength *= 0.5 + 0.5 * self.cluster.tightness_at(index)
        return float(strength)

    def distance_atr(self, price: float, atr: float, index: int) -> float:
        if not np.isfinite(atr) or atr <= 0:
            return float("nan")
        return abs(self.price_at(index) - price) / atr

    def as_dict(self, index: int) -> dict:
        return {
            "kind": self.kind.value,
            "side": self.side.value,
            "price": self.price_at(index),
            "status": self.status_at(index).value,
            "created_at": self.created_at,
            "confirmed_at": self.confirmed_at,
            "touches": self.touch_count_at(index),
            "members": self.member_count_at(index),
            "strength": self.strength_at(index),
            "origin": self.origin,
        }


@dataclass
class LiquidityMap:
    """Every pool found over one frame, with time-travel accessors."""

    pools: list[LiquidityPool] = field(default_factory=list)
    sessions: SessionSeries = field(default_factory=SessionSeries)
    n_bars: int = 0
    symbol: str = ""
    timeframe: str = ""
    config: LiquidityConfig = field(default_factory=LiquidityConfig)

    def known_at(self, index: int) -> list[LiquidityPool]:
        return [p for p in self.pools if p.is_known_at(index)]

    def intact_at(self, index: int) -> list[LiquidityPool]:
        return [p for p in self.pools if p.is_intact_at(index)]

    def above(self, price: float, index: int, *, intact_only: bool = True) -> list[LiquidityPool]:
        source = self.intact_at(index) if intact_only else self.known_at(index)
        pools = [p for p in source if p.side is Side.BUY_SIDE and p.price_at(index) > price]
        return sorted(pools, key=lambda p: p.price_at(index))

    def below(self, price: float, index: int, *, intact_only: bool = True) -> list[LiquidityPool]:
        source = self.intact_at(index) if intact_only else self.known_at(index)
        pools = [p for p in source if p.side is Side.SELL_SIDE and p.price_at(index) < price]
        return sorted(pools, key=lambda p: p.price_at(index), reverse=True)

    def nearest_above(self, price: float, index: int) -> LiquidityPool | None:
        pools = self.above(price, index)
        return pools[0] if pools else None

    def nearest_below(self, price: float, index: int) -> LiquidityPool | None:
        pools = self.below(price, index)
        return pools[0] if pools else None

    def counts(self, index: int | None = None) -> dict[str, int]:
        at = self.n_bars - 1 if index is None else index
        counts: dict[str, int] = {}
        for pool in self.known_at(at):
            counts[pool.kind.value] = counts.get(pool.kind.value, 0) + 1
        return counts

    def status_counts(self, index: int | None = None) -> dict[str, int]:
        at = self.n_bars - 1 if index is None else index
        counts: dict[str, int] = {}
        for pool in self.known_at(at):
            key = pool.status_at(at).value
            counts[key] = counts.get(key, 0) + 1
        return counts

    def to_frame(self, index: int | None = None) -> pd.DataFrame:
        at = self.n_bars - 1 if index is None else index
        rows = [p.as_dict(at) for p in self.known_at(at)]
        return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["kind", "side", "price"])


# ----------------------------------------------------------------- building

def _calendar_keys(index: pd.DatetimeIndex, config: LiquidityConfig) -> tuple[np.ndarray, np.ndarray]:
    """Day and week keys, honouring a broker day that does not start at 00:00."""
    shifted = index - pd.Timedelta(hours=config.day_start_hour)
    days = shifted.normalize()
    offsets = (days.weekday - config.week_start_weekday) % 7
    weeks = days - pd.to_timedelta(offsets, unit="D")
    return days.to_numpy(), weeks.to_numpy()


def _period_pools(
    frame: pd.DataFrame, keys: np.ndarray, high_kind: PoolKind, low_kind: PoolKind,
    config: LiquidityConfig, symbol: str, timeframe: str, atr_values: np.ndarray,
) -> list[LiquidityPool]:
    """Previous-period high/low pools, created at the first bar of the next period."""
    pools: list[LiquidityPool] = []
    highs = frame["high"].to_numpy("float64")
    lows = frame["low"].to_numpy("float64")

    boundaries = np.flatnonzero(keys[1:] != keys[:-1]) + 1
    start = 0
    for boundary in boundaries:
        block = slice(start, boundary)
        high_offset = int(np.argmax(highs[block]))
        low_offset = int(np.argmin(lows[block]))
        # Known from the first bar of the NEXT period, never before.
        for kind, price, source_index in (
            (high_kind, float(highs[block].max()), start + high_offset),
            (low_kind, float(lows[block].min()), start + low_offset),
        ):
            pools.append(LiquidityPool(
                kind=kind, side=_SIDE_BY_KIND[kind], price=price,
                created_at_index=source_index, confirmed_at_index=int(boundary),
                created_at=frame.index[source_index], confirmed_at=frame.index[boundary],
                origin=str(pd.Timestamp(keys[start]).date()),
                atr_at_creation=float(atr_values[source_index]),
                symbol=symbol, timeframe=timeframe, _config=config,
            ))
        start = int(boundary)
    return pools


def _session_pools(
    frame: pd.DataFrame, sessions: SessionSeries, config: LiquidityConfig,
    symbol: str, timeframe: str, atr_values: np.ndarray,
) -> list[LiquidityPool]:
    pools: list[LiquidityPool] = []
    n = len(frame)
    for instance in sessions.instances:
        confirmed = instance.end_index + 1
        if confirmed >= n:
            continue     # the session has not finished inside this frame
        for kind, price, source_index in (
            (PoolKind.SESSION_HIGH, instance.high, instance.high_index),
            (PoolKind.SESSION_LOW, instance.low, instance.low_index),
        ):
            pools.append(LiquidityPool(
                kind=kind, side=_SIDE_BY_KIND[kind], price=price,
                created_at_index=source_index, confirmed_at_index=confirmed,
                created_at=frame.index[source_index], confirmed_at=frame.index[confirmed],
                origin=instance.label,
                atr_at_creation=float(atr_values[source_index]),
                symbol=symbol, timeframe=timeframe, _config=config,
            ))
    return pools


def _apply_lifecycle(pool: LiquidityPool, highs: np.ndarray, lows: np.ndarray,
                     closes: np.ndarray, config: LiquidityConfig) -> None:
    """Walk forward from confirmation and record touches, sweep and consumption."""
    start = pool.confirmed_at_index
    n = len(highs)
    if start >= n:
        return

    atr = pool.atr_at_creation
    scale = atr if np.isfinite(atr) and atr > 0 else 0.0
    penetration = config.sweep_min_penetration_atr * scale
    touch_tol = config.touch_tolerance_atr * scale
    price = pool.price

    if pool.side.is_above:
        closed_beyond = np.flatnonzero(closes[start:] > price)
        wick_beyond = np.flatnonzero(highs[start:] > price + penetration)
        touched = highs[start:] >= price - touch_tol
    else:
        closed_beyond = np.flatnonzero(closes[start:] < price)
        wick_beyond = np.flatnonzero(lows[start:] < price - penetration)
        touched = lows[start:] <= price + touch_tol

    consumed = int(closed_beyond[0]) + start if closed_beyond.size else None
    swept = int(wick_beyond[0]) + start if wick_beyond.size else None

    # A wick beyond that is only ever accompanied by a close beyond is not a
    # sweep -- it is the level being taken out.
    if swept is not None and consumed is not None and swept >= consumed:
        swept = None

    pool.consumed_at_index = consumed
    pool.swept_at_index = swept

    limit = consumed if consumed is not None else n
    pool.touch_indices = [int(i) + start for i in np.flatnonzero(touched[: limit - start])]


def build_liquidity(
    frame: pd.DataFrame,
    rules: SMCRules = DEFAULT_RULES,
    *,
    swings: SwingSeries | None = None,
    sessions: SessionSeries | None = None,
    atr: pd.Series | None = None,
) -> LiquidityMap:
    """Find every liquidity pool over a frame and record its lifecycle."""
    config = rules.liquidity
    n = len(frame)
    symbol = str(frame["symbol"].iloc[0]) if n and "symbol" in frame else ""
    timeframe = str(frame["timeframe"].iloc[0]) if n and "timeframe" in frame else ""

    liquidity = LiquidityMap(n_bars=n, symbol=symbol, timeframe=timeframe, config=config)
    if n == 0:
        return liquidity

    atr_series = atr if atr is not None else wilder_atr(frame, rules.atr_period)
    atr_values = atr_series.to_numpy("float64")
    swing_series = swings if swings is not None else detect_swings(frame, rules, atr=atr_series)
    session_series = sessions if sessions is not None else build_sessions(frame, rules)
    liquidity.sessions = session_series

    pools: list[LiquidityPool] = []

    if config.track_swing_pools:
        for swing in swing_series.swings:
            kind = PoolKind.SWING_HIGH if swing.kind is SwingKind.HIGH else PoolKind.SWING_LOW
            pools.append(LiquidityPool(
                kind=kind, side=_SIDE_BY_KIND[kind], price=swing.price,
                created_at_index=swing.formed_at_index,
                confirmed_at_index=swing.confirmed_at_index,
                created_at=swing.formed_at, confirmed_at=swing.confirmed_at,
                origin="swing", atr_at_creation=swing.atr_at_formation,
                symbol=symbol, timeframe=timeframe, _config=config,
            ))

    if config.track_equal_levels:
        for cluster in find_equal_levels(swing_series, atr_values, config):
            confirmed = cluster.confirmed_at_index(config.equal_min_members)
            if confirmed is None or confirmed >= n:
                continue
            kind = PoolKind.EQH if cluster.kind is SwingKind.HIGH else PoolKind.EQL
            source_index = cluster.first_formed_index
            pools.append(LiquidityPool(
                kind=kind, side=_SIDE_BY_KIND[kind],
                price=cluster.price_at(confirmed),
                created_at_index=source_index, confirmed_at_index=confirmed,
                created_at=frame.index[source_index], confirmed_at=frame.index[confirmed],
                origin=f"{len(cluster.members)} members", cluster=cluster,
                atr_at_creation=float(atr_values[source_index]),
                symbol=symbol, timeframe=timeframe, _config=config,
            ))

    if config.track_daily or config.track_weekly:
        days, weeks = _calendar_keys(frame.index, config)
        if config.track_daily:
            pools += _period_pools(frame, days, PoolKind.PDH, PoolKind.PDL,
                                   config, symbol, timeframe, atr_values)
        if config.track_weekly:
            pools += _period_pools(frame, weeks, PoolKind.PWH, PoolKind.PWL,
                                   config, symbol, timeframe, atr_values)

    if config.track_sessions:
        pools += _session_pools(frame, session_series, config, symbol, timeframe, atr_values)

    highs = frame["high"].to_numpy("float64")
    lows = frame["low"].to_numpy("float64")
    closes = frame["close"].to_numpy("float64")
    for pool in pools:
        _apply_lifecycle(pool, highs, lows, closes, config)

    pools.sort(key=lambda p: (p.confirmed_at_index, p.kind.value, p.price))
    liquidity.pools = pools
    return liquidity
