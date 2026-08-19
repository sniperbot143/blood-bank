"""BOS, CHOCH and MSS -- three distinct events, not three names for one.

    BOS    a close beyond a structural level IN THE DIRECTION of bias.
           Continuation. Confirms the trend; never flips it.

    CHOCH  the FIRST close through the protected level AGAINST bias.
           A warning. Bias -> RANGE. Not an entry trigger.

    MSS    a CHOCH plus displacement plus a close break of a level that is a
           genuine structural low/high. Confirmed reversal. Bias flips.

Every MSS is a CHOCH; no CHOCH is automatically an MSS; BOS is a different
class entirely. They are stored as separate event types and never conflated
(docs/SMC_DEFINITIONS.md §§3-5).

Structural note: the planned tree had bos.py / choch.py / mss.py as separate
modules. They share one forward pass over bars with one bias state machine --
splitting that across three files would have meant three modules mutating each
other's state, so the detectors live here together and each has its own
predicate function, tests and event type.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import pandas as pd

from common.indicators import wilder_atr
from config.smc_rules import BOSMode, DEFAULT_RULES, BreakConfig, SMCRules
from structure.displacement import UNKNOWN, Displacement, displacement_run_at
from structure.market_structure import Bias, MarketStructure, iter_levels
from structure.swings import SwingPoint


class BreakType(str, Enum):
    BOS = "BOS"
    CHOCH = "CHOCH"
    MSS = "MSS"


class Direction(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"

    @property
    def bias(self) -> Bias:
        return Bias.BULLISH if self is Direction.BULLISH else Bias.BEARISH

    @property
    def opposite(self) -> "Direction":
        return Direction.BEARISH if self is Direction.BULLISH else Direction.BULLISH


@dataclass
class BreakEvent:
    """One confirmed structural break. `index` IS the confirmation bar."""

    type: BreakType
    direction: Direction
    index: int
    timestamp: pd.Timestamp
    broken_level: float
    broken_level_formed_index: int
    broken_level_time: pd.Timestamp
    break_price: float          # the close (or the wick, in WICK_OR_CLOSE mode)
    bias_before: Bias
    bias_after: Bias
    displacement: Displacement = UNKNOWN
    mode: BOSMode = BOSMode.CLOSE_ONLY
    choch_index: int | None = None      # for MSS: the CHOCH it was built on
    symbol: str = ""
    timeframe: str = ""

    def as_dict(self) -> dict:
        return {
            "type": self.type.value,
            "direction": self.direction.value,
            "index": self.index,
            "timestamp": self.timestamp,
            "broken_level": self.broken_level,
            "broken_level_time": self.broken_level_time,
            "break_price": self.break_price,
            "bias_before": self.bias_before.value,
            "bias_after": self.bias_after.value,
            "displacement_score": self.displacement.score,
            "displacement_class": self.displacement.grade.value,
        }


@dataclass
class PendingCHOCH:
    """A CHOCH waiting to see whether displacement turns it into an MSS."""

    direction: Direction
    level: float
    level_formed_index: int
    level_time: pd.Timestamp
    index: int
    expires_at: int


@dataclass
class BreakSeries:
    """All breaks over one frame, plus the break-confirmed bias timeline."""

    events: list[BreakEvent] = field(default_factory=list)
    bias_by_bar: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=object))
    n_bars: int = 0
    symbol: str = ""
    timeframe: str = ""
    config: BreakConfig = field(default_factory=BreakConfig)
    expired_choch: int = 0

    def events_known_at(self, index: int) -> list[BreakEvent]:
        return [e for e in self.events if e.index <= index]

    def bias_at(self, index: int) -> Bias:
        if not self.n_bars:
            return Bias.RANGE
        return self.bias_by_bar[max(0, min(index, self.n_bars - 1))]

    _last_any: list | None = None

    def last(self, type_: BreakType | None = None, index: int | None = None) -> BreakEvent | None:
        at = self.n_bars - 1 if index is None else index
        if type_ is None:
            # Hot path (every bar of every scan): cache the newest event per bar.
            if self._last_any is None:
                cache: list = [None] * max(self.n_bars, 1)
                current = None
                cursor = 0
                ordered = sorted(self.events, key=lambda e: e.index)
                for t in range(self.n_bars):
                    while cursor < len(ordered) and ordered[cursor].index <= t:
                        current = ordered[cursor]
                        cursor += 1
                    cache[t] = current
                self._last_any = cache
            if not self.n_bars:
                return None
            return self._last_any[max(0, min(at, self.n_bars - 1))]

        for event in reversed(self.events):
            if event.index <= at and event.type is type_:
                return event
        return None

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for event in self.events:
            key = f"{event.type.value}_{event.direction.value}"
            counts[key] = counts.get(key, 0) + 1
        return counts

    def to_frame(self) -> pd.DataFrame:
        rows = [e.as_dict() for e in self.events]
        return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["type", "direction", "index"])


# ------------------------------------------------------------- predicates

def breaks_level(high: float, low: float, close: float, level: float,
                 *, direction: Direction, mode: BOSMode) -> tuple[bool, float]:
    """Did this bar go through `level` in `direction`? Returns (broken, price).

    CLOSE_ONLY (and DISPLACEMENT_CONFIRMATION) require a close beyond the
    level; WICK_OR_CLOSE accepts a touch. A wick through a level is liquidity
    being taken, not structure breaking -- which is why CLOSE_ONLY is default.
    """
    if mode is BOSMode.WICK_OR_CLOSE:
        if direction is Direction.BULLISH and high > level:
            return True, high
        if direction is Direction.BEARISH and low < level:
            return True, low
        return False, float("nan")

    if direction is Direction.BULLISH and close > level:
        return True, close
    if direction is Direction.BEARISH and close < level:
        return True, close
    return False, float("nan")


def _is_valid_structural_level(ordinal: int | None, config: BreakConfig) -> bool:
    """An MSS must break a level with real structure behind it, not the first
    swing in the file."""
    return ordinal is not None and ordinal >= config.mss_min_legs


# ---------------------------------------------------------------- engine

def _origin_ok(sweeps, direction: Direction, index: int, rules: SMCRules) -> bool:
    """MSS origin test (SMC_DEFINITIONS §5.4): did the move start from a sweep?

    A bearish shift should originate from buy-side liquidity being taken --
    stops above the highs swept, then the reversal. Enforced only when
    `mss_require_swept_origin` is on AND a SweepSeries was supplied; without
    sweeps the requirement cannot be judged, so it is skipped rather than
    silently failed.
    """
    if not rules.breaks.mss_require_swept_origin or sweeps is None:
        return True
    from liquidity.sweeps import SweepType

    wanted = (SweepType.BUY_SIDE_SWEEP if direction is Direction.BEARISH
              else SweepType.SELL_SIDE_SWEEP)
    return bool(sweeps.recent(index, rules.sweeps.origin_lookback_bars, wanted))


def detect_breaks(
    frame: pd.DataFrame,
    structure: MarketStructure,
    rules: SMCRules = DEFAULT_RULES,
    *,
    atr: pd.Series | None = None,
    sweeps=None,
) -> BreakSeries:
    """Forward pass over closed bars, emitting BOS / CHOCH / MSS.

    Bar `t` is judged only against levels confirmed at or before `t`, so an
    event's `index` is genuinely the first bar it could have been known.
    """
    config = rules.breaks
    n = len(frame)
    series = BreakSeries(n_bars=n, symbol=structure.symbol,
                         timeframe=structure.timeframe, config=config)
    if n == 0:
        return series

    atr_values = (atr if atr is not None else wilder_atr(frame, rules.atr_period)).to_numpy("float64")
    highs = frame["high"].to_numpy("float64")
    lows = frame["low"].to_numpy("float64")
    closes = frame["close"].to_numpy("float64")
    gap_before = (frame["gap_before"].to_numpy(bool) if "gap_before" in frame.columns
                  else np.zeros(n, dtype=bool))
    timestamps = frame.index

    # Confirmation-order rank of each swing, for the MSS "real structure" test.
    ordinal: dict[int, int] = {
        item.formed_at_index: rank
        for rank, item in enumerate(
            sorted(structure.labels, key=lambda l: (l.confirmed_at_index, l.formed_at_index))
        )
    }

    bias = Bias.RANGE
    bias_by_bar = np.empty(n, dtype=object)
    pending: PendingCHOCH | None = None
    consumed_high: int | None = None     # formed index of the last high broken by BOS
    consumed_low: int | None = None
    choch_epoch_level: int | None = None  # protected level already CHOCH'd in this bias run

    for snapshot in iter_levels(structure):
        t = snapshot.index
        skip_bar = bool(gap_before[t]) and config.reject_on_gap_bar
        # Precedence within a bar: MSS > CHOCH > BOS. A bar that changes the
        # trend does not also "continue" it, and a level already broken this
        # bar is not fresh evidence.
        reversal_fired = False

        if pending is not None and t > pending.expires_at:
            pending = None
            series.expired_choch += 1

        if not skip_bar:
            # --- 1. MSS: does a pending CHOCH now have displacement behind it?
            if pending is not None:
                broken, price = breaks_level(highs[t], lows[t], closes[t], pending.level,
                                             direction=pending.direction, mode=BOSMode.CLOSE_ONLY)
                if broken:
                    disp = displacement_run_at(frame, t, bullish=pending.direction is Direction.BULLISH,
                                           atr_value=atr_values[t], config=rules.displacement)
                    valid = _is_valid_structural_level(
                        ordinal.get(pending.level_formed_index), config
                    )
                    origin_ok = _origin_ok(sweeps, pending.direction, t, rules)
                    if (np.isfinite(disp.score) and disp.score >= config.mss_min_displacement
                            and valid and origin_ok):
                        before = bias
                        bias = pending.direction.bias
                        series.events.append(BreakEvent(
                            type=BreakType.MSS, direction=pending.direction, index=t,
                            timestamp=timestamps[t], broken_level=pending.level,
                            broken_level_formed_index=pending.level_formed_index,
                            broken_level_time=pending.level_time, break_price=price,
                            bias_before=before, bias_after=bias, displacement=disp,
                            mode=BOSMode.CLOSE_ONLY, choch_index=pending.index,
                            symbol=series.symbol, timeframe=series.timeframe,
                        ))
                        # The level just broken is spent; the opposite side is
                        # fresh again now that bias has flipped.
                        if pending.direction is Direction.BEARISH:
                            consumed_low, consumed_high = pending.level_formed_index, None
                        else:
                            consumed_high, consumed_low = pending.level_formed_index, None
                        pending = None
                        choch_epoch_level = None
                        reversal_fired = True

            # --- 2. CHOCH: first close through the protected level against bias
            if pending is None and bias in (Bias.BULLISH, Bias.BEARISH):
                against = Direction.BEARISH if bias is Bias.BULLISH else Direction.BULLISH
                level_swing = (snapshot.protected_low if bias is Bias.BULLISH
                               else snapshot.protected_high)
                if (level_swing is not None
                        and level_swing.formed_at_index != choch_epoch_level):
                    broken, price = breaks_level(highs[t], lows[t], closes[t], level_swing.price,
                                                 direction=against, mode=BOSMode.CLOSE_ONLY)
                    if broken:
                        before = bias
                        bias = Bias.RANGE
                        choch_epoch_level = level_swing.formed_at_index
                        series.events.append(BreakEvent(
                            type=BreakType.CHOCH, direction=against, index=t,
                            timestamp=timestamps[t], broken_level=level_swing.price,
                            broken_level_formed_index=level_swing.formed_at_index,
                            broken_level_time=level_swing.formed_at, break_price=price,
                            bias_before=before, bias_after=bias,
                            displacement=displacement_run_at(
                                frame, t, bullish=against is Direction.BULLISH,
                                atr_value=atr_values[t], config=rules.displacement),
                            mode=BOSMode.CLOSE_ONLY,
                            symbol=series.symbol, timeframe=series.timeframe,
                        ))
                        pending = PendingCHOCH(
                            direction=against, level=level_swing.price,
                            level_formed_index=level_swing.formed_at_index,
                            level_time=level_swing.formed_at, index=t,
                            expires_at=t + config.mss_confirm_window,
                        )
                        # An MSS may confirm on this same bar.
                        disp = series.events[-1].displacement
                        valid = _is_valid_structural_level(
                            ordinal.get(level_swing.formed_at_index), config)
                        if (np.isfinite(disp.score) and disp.score >= config.mss_min_displacement
                                and valid and _origin_ok(sweeps, against, t, rules)):
                            bias = against.bias
                            series.events.append(BreakEvent(
                                type=BreakType.MSS, direction=against, index=t,
                                timestamp=timestamps[t], broken_level=level_swing.price,
                                broken_level_formed_index=level_swing.formed_at_index,
                                broken_level_time=level_swing.formed_at, break_price=price,
                                bias_before=Bias.RANGE, bias_after=bias, displacement=disp,
                                mode=BOSMode.CLOSE_ONLY, choch_index=t,
                                symbol=series.symbol, timeframe=series.timeframe,
                            ))
                            if against is Direction.BEARISH:
                                consumed_low, consumed_high = level_swing.formed_at_index, None
                            else:
                                consumed_high, consumed_low = level_swing.formed_at_index, None
                            pending = None
                            choch_epoch_level = None
                        else:
                            # A CHOCH without displacement still spends the level.
                            if against is Direction.BEARISH:
                                consumed_low = level_swing.formed_at_index
                            else:
                                consumed_high = level_swing.formed_at_index
                        reversal_fired = True

            # --- 3. BOS: continuation through a structural level
            if pending is None and not reversal_fired:
                for direction, level_swing, consumed in (
                    (Direction.BULLISH, snapshot.structural_high, consumed_high),
                    (Direction.BEARISH, snapshot.structural_low, consumed_low),
                ):
                    if level_swing is None or level_swing.formed_at_index == consumed:
                        continue
                    if bias is not Bias.RANGE and bias is not direction.bias:
                        continue    # a break against bias is CHOCH territory, not BOS

                    broken, price = breaks_level(highs[t], lows[t], closes[t], level_swing.price,
                                                 direction=direction, mode=config.bos_mode)
                    if not broken:
                        continue

                    disp = displacement_run_at(frame, t, bullish=direction is Direction.BULLISH,
                                           atr_value=atr_values[t], config=rules.displacement)
                    if (config.bos_mode is BOSMode.DISPLACEMENT_CONFIRMATION
                            and not (np.isfinite(disp.score)
                                     and disp.score >= config.mss_min_displacement)):
                        continue

                    before = bias
                    bias = direction.bias
                    if direction is Direction.BULLISH:
                        consumed_high = level_swing.formed_at_index
                    else:
                        consumed_low = level_swing.formed_at_index
                    choch_epoch_level = None
                    series.events.append(BreakEvent(
                        type=BreakType.BOS, direction=direction, index=t,
                        timestamp=timestamps[t], broken_level=level_swing.price,
                        broken_level_formed_index=level_swing.formed_at_index,
                        broken_level_time=level_swing.formed_at, break_price=price,
                        bias_before=before, bias_after=bias, displacement=disp,
                        mode=config.bos_mode,
                        symbol=series.symbol, timeframe=series.timeframe,
                    ))
                    break   # at most one BOS per bar

        bias_by_bar[t] = bias

    series.bias_by_bar = bias_by_bar
    return series
