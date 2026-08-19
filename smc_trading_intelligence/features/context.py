"""MarketContext -- every detector, built once, queryable at any bar.

Phases 2-11 each produce their own series. Everything after this point (setups,
features, probability, decisions, backtests) needs all of them together, at a
specific bar, with the as-of rules already enforced. Building them separately
in each consumer would be slow and would invite someone to forget a
`confirmed_at_index` check.

    context = MarketContext.build(frame, rules)
    snapshot = context.at(t)     # everything knowable at bar t

The context is built once over the whole frame; `at(t)` filters. That is
equivalent to rebuilding on `frame[:t+1]` because every underlying series is
as-of honest, and `test_no_lookahead.py` asserts exactly that equivalence.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from common.indicators import wilder_atr
from config.smc_rules import DEFAULT_RULES, SMCRules
from context.market_regime import Regime, RegimeSeries, build_regimes
from context.mtf_bias import MTFContext, build_mtf
from context.premium_discount import DealingRange, dealing_range_series
from imbalance.fvg import FairValueGap, FVGSeries, detect_fvgs
from imbalance.ifvg import IFVGSeries, detect_ifvgs
from liquidity.levels import LiquidityMap, LiquidityPool, build_liquidity
from liquidity.sweeps import SweepEvent, SweepSeries, detect_sweeps
from orderblocks.order_blocks import OrderBlock, OrderBlockSeries, detect_order_blocks
from structure.breaks import BreakEvent, BreakSeries, detect_breaks
from structure.market_structure import Bias, MarketStructure, StructureState, build_structure
from structure.swings import SwingSeries


@dataclass
class Snapshot:
    """Everything the engine knows at one bar. Nothing here postdates it.

    The three collection fields are lazy: scanning every pool at every bar is
    O(n x pools), which dominates a full-history run, and most consumers only
    want the nearest one or two.
    """

    index: int
    timestamp: pd.Timestamp
    open: float
    high: float
    low: float
    close: float
    atr: float
    bias: Bias
    structure: StructureState
    dealing_range: DealingRange
    regime: Regime
    session: str
    htf_bias: dict[str, str]
    last_break: BreakEvent | None
    last_sweep: SweepEvent | None
    _context: "MarketContext | None" = None

    @property
    def active_fvgs(self) -> list[FairValueGap]:
        return self._context.fvgs.active_at(self.index) if self._context else []

    @property
    def tradeable_obs(self) -> list[OrderBlock]:
        return self._context.order_blocks.tradeable_at(self.index) if self._context else []

    @property
    def intact_pools(self) -> list[LiquidityPool]:
        return self._context.liquidity.intact_at(self.index) if self._context else []


@dataclass
class MarketContext:
    """All detector output for one symbol/timeframe, with as-of accessors."""

    frame: pd.DataFrame
    rules: SMCRules
    atr: pd.Series
    swings: SwingSeries
    structure: MarketStructure
    breaks: BreakSeries
    liquidity: LiquidityMap
    sweeps: SweepSeries
    fvgs: FVGSeries
    ifvgs: IFVGSeries
    order_blocks: OrderBlockSeries
    ranges: list[DealingRange]
    regimes: RegimeSeries
    mtf: MTFContext
    symbol: str = ""
    timeframe: str = ""
    states: list[StructureState] = field(default_factory=list)

    @property
    def n_bars(self) -> int:
        return len(self.frame)

    @classmethod
    def build(
        cls,
        frame: pd.DataFrame,
        rules: SMCRules = DEFAULT_RULES,
        *,
        with_mtf: bool = True,
    ) -> "MarketContext":
        atr = wilder_atr(frame, rules.atr_period)
        symbol = str(frame["symbol"].iloc[0]) if len(frame) else ""
        timeframe = str(frame["timeframe"].iloc[0]) if len(frame) else ""

        structure = build_structure(frame, rules, atr=atr)
        liquidity = build_liquidity(frame, rules, swings=structure.swings, atr=atr)
        sweeps = detect_sweeps(frame, liquidity, rules, structure=structure, atr=atr)
        # Breaks are detected with sweeps available, so the MSS origin rule can
        # be enforced when it is switched on.
        breaks = detect_breaks(frame, structure, rules, atr=atr, sweeps=sweeps)
        structure.attach_breaks(breaks)

        fvgs = detect_fvgs(frame, rules, atr=atr)
        ifvgs = detect_ifvgs(frame, fvgs, rules, atr=atr)
        order_blocks = detect_order_blocks(frame, breaks, rules, structure=structure, atr=atr)
        ranges = dealing_range_series(frame, structure, rules, atr=atr)
        regimes = build_regimes(frame, structure, rules, atr=atr)
        mtf = build_mtf(frame, rules=rules) if with_mtf else MTFContext(ltf_timeframe=timeframe)

        from structure.market_structure import iter_states

        states = list(iter_states(structure, atr.to_numpy("float64"), frame.index))

        return cls(
            frame=frame, rules=rules, atr=atr, swings=structure.swings, structure=structure,
            breaks=breaks, liquidity=liquidity, sweeps=sweeps, fvgs=fvgs, ifvgs=ifvgs,
            order_blocks=order_blocks, ranges=ranges, regimes=regimes, mtf=mtf,
            symbol=symbol, timeframe=timeframe, states=states,
        )

    # -- as-of accessors ---------------------------------------------------

    def atr_at(self, index: int) -> float:
        return float(self.atr.iloc[index]) if 0 <= index < self.n_bars else float("nan")

    def range_at(self, index: int) -> DealingRange:
        from context.premium_discount import NO_RANGE

        return self.ranges[index] if 0 <= index < len(self.ranges) else NO_RANGE

    def at(self, index: int) -> Snapshot:
        """Everything knowable at bar `index`."""
        if not (0 <= index < self.n_bars):
            raise IndexError(f"bar {index} out of range for {self.n_bars} bars")
        row = self.frame.iloc[index]
        state = (self.states[index] if index < len(self.states)
                 else self.structure.state_at(index))
        return Snapshot(
            index=index,
            timestamp=self.frame.index[index],
            open=float(row["open"]), high=float(row["high"]),
            low=float(row["low"]), close=float(row["close"]),
            atr=self.atr_at(index),
            bias=state.bias,
            structure=state,
            dealing_range=self.range_at(index),
            regime=self.regimes.at(index),
            session=self.liquidity.sessions.session_at(index),
            htf_bias=self.mtf.summary_at(index),
            last_break=self.breaks.last(index=index),
            last_sweep=self.sweeps.last(index=index),
            _context=self,
        )

    def describe(self, index: int) -> str:
        snap = self.at(index)
        return "\n".join([
            f"bar {index} ({snap.timestamp:%Y-%m-%d %H:%M} UTC) close {snap.close:.5f}",
            f"bias            : {snap.bias.value}   HTF: {snap.htf_bias}",
            f"regime          : {snap.regime.key}",
            f"zone            : {snap.dealing_range.zone.value} "
            f"(pos {snap.dealing_range.position:.2f})" if snap.dealing_range.is_valid
            else "zone            : NO_RANGE",
            f"session         : {snap.session or '-'}",
            f"last break      : "
            f"{snap.last_break.type.value + ' ' + snap.last_break.direction.value if snap.last_break else '-'}",
            f"last sweep      : {snap.last_sweep.type.value if snap.last_sweep else '-'}",
            f"active FVGs     : {len(snap.active_fvgs)}",
            f"tradeable OBs   : {len(snap.tradeable_obs)}",
            f"intact pools    : {len(snap.intact_pools)}",
        ])
