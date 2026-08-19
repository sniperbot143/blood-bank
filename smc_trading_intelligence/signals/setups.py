"""Setup construction: the pre-registered families, and nothing else.

`SMC_DEFINITIONS.md` §15 fixes five families BEFORE any statistics are run, so
the probability engine cannot be handed a taxonomy that was reverse-engineered
from the answers:

    SWEEP_MSS_OB / SWEEP_MSS_FVG / SWEEP_MSS_OB_FVG
    BOS_CONTINUATION_FVG
    BREAKER_RETEST

A candidate is built from `MarketContext.at(t)` alone, so its `signal_index`
is genuinely the first bar it could have been issued. Anything the detector
sees that is not one of the five is recorded as `OTHER` and is not tradeable
in v0.1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import pandas as pd

from config.smc_rules import DEFAULT_RULES, SMCRules, SetupConfig
from features.context import MarketContext, Snapshot
from features.feature_engineering import FeatureSet, extract_features
from imbalance.fvg import FVGDirection
from liquidity.sweeps import SweepType
from orderblocks.order_blocks import OBDirection, OBStatus
from risk.levels import TradeLevels, build_levels
from structure.breaks import BreakType, Direction


class SetupFamily(str, Enum):
    SWEEP_MSS_OB = "SWEEP_MSS_OB"
    SWEEP_MSS_FVG = "SWEEP_MSS_FVG"
    SWEEP_MSS_OB_FVG = "SWEEP_MSS_OB_FVG"
    BOS_CONTINUATION_FVG = "BOS_CONTINUATION_FVG"
    BREAKER_RETEST = "BREAKER_RETEST"

    @property
    def is_reversal(self) -> bool:
        return self.value.startswith("SWEEP_MSS")


@dataclass
class SetupCandidate:
    """One candidate trade, complete enough to be labelled and stored."""

    family: SetupFamily
    setup_type: str                 # the full taxonomy string
    direction: str                  # "BUY" / "SELL"
    signal_index: int               # the bar it became issuable
    signal_time: pd.Timestamp
    levels: TradeLevels
    features: FeatureSet
    symbol: str = ""
    timeframe: str = ""
    superseded: bool = False        # an earlier setup was still open
    notes: list[str] = field(default_factory=list)

    @property
    def bullish(self) -> bool:
        return self.direction == "BUY"

    def as_dict(self) -> dict:
        data = {
            "family": self.family.value,
            "setup_type": self.setup_type,
            "direction": self.direction,
            "signal_index": self.signal_index,
            "signal_time": self.signal_time,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "superseded": int(self.superseded),
        }
        data.update(self.levels.as_dict())
        return data


@dataclass
class SetupSeries:
    candidates: list[SetupCandidate] = field(default_factory=list)
    rejected: dict[str, int] = field(default_factory=dict)
    n_bars: int = 0
    symbol: str = ""
    timeframe: str = ""

    def tradeable(self) -> list[SetupCandidate]:
        return [c for c in self.candidates if not c.superseded]

    def known_at(self, index: int) -> list[SetupCandidate]:
        return [c for c in self.candidates if c.signal_index <= index]

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for candidate in self.candidates:
            key = f"{candidate.family.value}|{candidate.direction}"
            counts[key] = counts.get(key, 0) + 1
        return counts

    def to_frame(self) -> pd.DataFrame:
        rows = [c.as_dict() for c in self.candidates]
        return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["family", "direction"])


def _reject(series: SetupSeries, reason: str) -> None:
    series.rejected[reason] = series.rejected.get(reason, 0) + 1


def _build_candidate(
    context: MarketContext, snap: Snapshot, *, bullish: bool, family: SetupFamily,
    poi_top: float, poi_bottom: float, protective: float, stop_source: str,
    config: SetupConfig, suffix: str = "",
) -> SetupCandidate | None:
    entry = ((poi_top + poi_bottom) / 2.0 if config.entry_at_poi_mid
             else (poi_top if bullish else poi_bottom))
    levels = build_levels(context, snap.index, bullish=bullish, entry=entry,
                          protective_price=protective, stop_source=stop_source,
                          config=config)
    if not levels.is_valid:
        return None

    features = extract_features(
        context, snap.index, direction_bullish=bullish, snapshot=snap,
        entry=levels.entry, stop_loss=levels.stop_loss, take_profit=levels.take_profit_1,
    )
    zone = snap.dealing_range.zone.value
    setup_type = f"{family.value}{suffix}|{'BUY' if bullish else 'SELL'}|{zone}"
    return SetupCandidate(
        family=family, setup_type=setup_type, direction="BUY" if bullish else "SELL",
        signal_index=snap.index, signal_time=snap.timestamp, levels=levels,
        features=features, symbol=context.symbol, timeframe=context.timeframe,
    )


def detect_setups(
    context: MarketContext,
    rules: SMCRules = DEFAULT_RULES,
) -> SetupSeries:
    """Scan every bar for the five pre-registered families."""
    config = rules.setups
    series = SetupSeries(n_bars=context.n_bars, symbol=context.symbol,
                         timeframe=context.timeframe)
    if context.n_bars == 0:
        return series

    # Which breakers are "recently flipped" at each bar, precomputed once:
    # scanning every block at every bar is the dominant cost otherwise.
    recent_breakers: dict[int, list] = {}
    for block in context.order_blocks.blocks:
        if block.breaker_at_index is None:
            continue
        start = block.breaker_at_index
        for bar in range(start, min(start + config.break_lookback + 1, context.n_bars)):
            recent_breakers.setdefault(bar, []).append(block)

    for t in range(context.n_bars):
        snap = context.at(t)
        atr = snap.atr
        if not (atr == atr) or atr <= 0:      # NaN check without importing math
            continue

        candidate = (_reversal_candidate(context, snap, config, series)
                     or _continuation_candidate(context, snap, config, series)
                     or _breaker_candidate(context, snap, config, series,
                                           recent_breakers.get(t, [])))
        if candidate is not None:
            series.candidates.append(candidate)

    if config.deoverlap:
        _mark_superseded(series, config)
    return series


def _reversal_candidate(context: MarketContext, snap: Snapshot, config: SetupConfig,
                        series: SetupSeries) -> SetupCandidate | None:
    """SWEEP -> MSS -> retrace into an OB and/or FVG."""
    event = snap.last_break
    if event is None or event.type is not BreakType.MSS:
        return None
    if snap.index - event.index > config.break_lookback:
        return None

    bullish = event.direction is Direction.BULLISH
    wanted_sweep = SweepType.SELL_SIDE_SWEEP if bullish else SweepType.BUY_SIDE_SWEEP
    sweeps = context.sweeps.recent(snap.index, config.sweep_lookback, wanted_sweep)
    if not sweeps:
        _reject(series, "MSS_WITHOUT_SWEEP")
        return None
    sweep = sweeps[-1]

    ob_direction = OBDirection.BULLISH if bullish else OBDirection.BEARISH
    fvg_direction = FVGDirection.BULLISH if bullish else FVGDirection.BEARISH
    block = context.order_blocks.nearest(snap.close, snap.index, ob_direction)
    gap = context.fvgs.nearest(snap.close, snap.index, fvg_direction)

    if block is not None and abs(block.mid - snap.close) > config.poi_max_distance_atr * snap.atr:
        block = None
    if gap is not None and abs(gap.mid - snap.close) > config.poi_max_distance_atr * snap.atr:
        gap = None
    if block is None and gap is None:
        _reject(series, "NO_POI_IN_RANGE")
        return None

    if block is not None and gap is not None:
        family = SetupFamily.SWEEP_MSS_OB_FVG
        top = min(block.top, gap.top) if bullish else max(block.top, gap.top)
        bottom = max(block.bottom, gap.bottom) if bullish else min(block.bottom, gap.bottom)
        top, bottom = max(top, bottom), min(top, bottom)
    elif block is not None:
        family, top, bottom = SetupFamily.SWEEP_MSS_OB, block.top, block.bottom
    else:
        family, top, bottom = SetupFamily.SWEEP_MSS_FVG, gap.top, gap.bottom

    # The sweep extreme is what must not be revisited.
    protective = min(sweep.extreme, bottom) if bullish else max(sweep.extreme, top)
    candidate = _build_candidate(
        context, snap, bullish=bullish, family=family, poi_top=top, poi_bottom=bottom,
        protective=protective, stop_source="SWEEP_EXTREME", config=config,
    )
    if candidate is None:
        _reject(series, "GEOMETRY_INVALID")
    return candidate


def _continuation_candidate(context: MarketContext, snap: Snapshot, config: SetupConfig,
                            series: SetupSeries) -> SetupCandidate | None:
    """HTF-aligned BOS -> retrace into the FVG the leg left behind."""
    event = snap.last_break
    if event is None or event.type is not BreakType.BOS:
        return None
    if snap.index - event.index > config.break_lookback:
        return None

    bullish = event.direction is Direction.BULLISH
    if context.rules.mtf.htf_veto and context.mtf.conflicts(snap.index, bullish):
        _reject(series, "HTF_CONFLICT")
        return None

    fvg_direction = FVGDirection.BULLISH if bullish else FVGDirection.BEARISH
    gap = context.fvgs.nearest(snap.close, snap.index, fvg_direction)
    if gap is None or abs(gap.mid - snap.close) > config.poi_max_distance_atr * snap.atr:
        _reject(series, "NO_FVG_IN_RANGE")
        return None

    protective = gap.bottom if bullish else gap.top
    candidate = _build_candidate(
        context, snap, bullish=bullish, family=SetupFamily.BOS_CONTINUATION_FVG,
        poi_top=gap.top, poi_bottom=gap.bottom, protective=protective,
        stop_source="FVG_EDGE", config=config,
    )
    if candidate is None:
        _reject(series, "GEOMETRY_INVALID")
    return candidate


def _breaker_candidate(context: MarketContext, snap: Snapshot, config: SetupConfig,
                       series: SetupSeries, breakers: list) -> SetupCandidate | None:
    """A failed order block, flipped, being retested."""
    if not breakers:
        return None

    for block in reversed(breakers):
        bullish = block.breaker_direction() is OBDirection.BULLISH
        if abs(block.mid - snap.close) > config.poi_max_distance_atr * snap.atr:
            continue
        protective = block.bottom if bullish else block.top
        candidate = _build_candidate(
            context, snap, bullish=bullish, family=SetupFamily.BREAKER_RETEST,
            poi_top=block.top, poi_bottom=block.bottom, protective=protective,
            stop_source="BREAKER_EDGE", config=config,
        )
        if candidate is not None:
            return candidate
    return None


def _mark_superseded(series: SetupSeries, config: SetupConfig) -> None:
    """One open setup per (symbol, direction) -- the rest are census only.

    Overlapping M5 setups measure the same price move several times, which
    inflates the effective sample size the probability engine later relies on
    (KNOWN_ISSUES #1). They are kept, but flagged out of the estimates.
    """
    open_until: dict[str, int] = {}
    for candidate in series.candidates:
        key = candidate.direction
        busy_until = open_until.get(key, -1)
        if candidate.signal_index <= busy_until:
            candidate.superseded = True
            candidate.notes.append("overlaps an earlier open setup")
        else:
            open_until[key] = candidate.signal_index + config.max_hold_bars
