"""Multi-timeframe alignment: HTF bias without leaking the HTF future.

The trap: an H1 bar that opens at 09:00 is not known until 10:00. Reading its
close on the 09:05 M5 bar is a one-hour look-ahead, and it is the single most
common way an SMC backtest fools itself.

The rule here: HTF bar `h` may be used at LTF bar `t` only when

    htf_open[h] + htf_interval <= ltf_open[t] + ltf_interval

i.e. the HTF bar has actually finished by the time the LTF bar closes.
`align_htf()` returns that mapping and `test_no_lookahead.py` asserts it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from config.settings import get_timeframe
from config.smc_rules import DEFAULT_RULES, SMCRules
from data.normalizer import CANONICAL_COLUMNS
from structure.market_structure import Bias, MarketStructure, build_structure


def resample_frame(frame: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Aggregate a canonical LTF frame up to a higher timeframe.

    Only *complete* HTF buckets are kept: a partially filled last bucket would
    be a forming bar, which nothing downstream is allowed to see.
    """
    tf = get_timeframe(timeframe)
    if frame.empty:
        return frame.copy()

    rule = f"{tf.minutes}min"
    agg = frame.resample(rule, label="left", closed="left").agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), tick_volume=("tick_volume", "sum"),
        real_volume=("real_volume", "sum"), spread=("spread", "mean"),
    ).dropna(subset=["open", "high", "low", "close"])

    if agg.empty:
        return agg

    source_tf = get_timeframe(str(frame["timeframe"].iloc[0]))
    expected = tf.minutes // source_tf.minutes
    counts = frame.resample(rule, label="left", closed="left").size()
    complete = counts.reindex(agg.index).fillna(0) >= expected
    agg = agg[complete.to_numpy()]
    if agg.empty:
        # Every bucket was partial -- e.g. resampling to a timeframe the source
        # cannot fill. Return early: the gap_before line below builds a
        # length-1 array from an empty index and would resurrect a phantom bar.
        return agg

    agg["spread"] = agg["spread"].round().astype("int32")
    agg["tick_volume"] = agg["tick_volume"].astype("int64")
    agg["real_volume"] = agg["real_volume"].astype("int64")
    agg["symbol"] = frame["symbol"].iloc[0]
    agg["timeframe"] = tf.name
    agg["is_closed"] = True
    step = pd.Timedelta(minutes=tf.minutes)
    agg["gap_before"] = np.r_[False, (agg.index[1:] - agg.index[:-1]) > step]
    agg.index.name = "timestamp"
    return agg[CANONICAL_COLUMNS]


def align_htf(ltf_index: pd.DatetimeIndex, ltf_timeframe: str,
              htf_index: pd.DatetimeIndex, htf_timeframe: str) -> np.ndarray:
    """For each LTF bar, the newest HTF bar that had already CLOSED by then.

    Returns an int array of HTF positions, -1 where no HTF bar is usable yet.
    """
    ltf_close = ltf_index + get_timeframe(ltf_timeframe).delta
    htf_close = htf_index + get_timeframe(htf_timeframe).delta
    # searchsorted with side="right" gives the count of HTF bars that closed
    # at or before each LTF close; minus one is the newest usable index.
    positions = np.searchsorted(htf_close.to_numpy(), ltf_close.to_numpy(), side="right") - 1
    return positions.astype(int)


@dataclass
class MTFView:
    """One higher timeframe, aligned onto the LTF bar index."""

    timeframe: str
    frame: pd.DataFrame
    structure: MarketStructure
    mapping: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=int))

    def htf_index_at(self, index: int) -> int:
        if not len(self.mapping):
            return -1
        return int(self.mapping[max(0, min(index, len(self.mapping) - 1))])

    def bias_at(self, index: int) -> Bias:
        htf = self.htf_index_at(index)
        return Bias.RANGE if htf < 0 else self.structure.bias_at(htf)

    def state_at(self, index: int):
        htf = self.htf_index_at(index)
        return None if htf < 0 else self.structure.state_at(htf)

    def bar_time_at(self, index: int) -> pd.Timestamp | None:
        htf = self.htf_index_at(index)
        return None if htf < 0 else self.frame.index[htf]


@dataclass
class MTFContext:
    """HTF and MTF views aligned to one LTF frame."""

    ltf_timeframe: str
    views: dict[str, MTFView] = field(default_factory=dict)

    def view(self, timeframe: str) -> MTFView | None:
        return self.views.get(get_timeframe(timeframe).name)

    def bias_at(self, timeframe: str, index: int) -> Bias:
        view = self.view(timeframe)
        return Bias.RANGE if view is None else view.bias_at(index)

    def aligned(self, index: int, direction_bullish: bool) -> bool:
        """True when every tracked higher timeframe agrees with the direction."""
        wanted = Bias.BULLISH if direction_bullish else Bias.BEARISH
        return all(view.bias_at(index) is wanted for view in self.views.values())

    def conflicts(self, index: int, direction_bullish: bool) -> list[str]:
        """Which higher timeframes actively oppose the direction."""
        opposed = Bias.BEARISH if direction_bullish else Bias.BULLISH
        return [tf for tf, view in self.views.items() if view.bias_at(index) is opposed]

    def summary_at(self, index: int) -> dict[str, str]:
        return {tf: view.bias_at(index).value for tf, view in self.views.items()}


def build_mtf(
    frame: pd.DataFrame,
    higher_timeframes: list[str] | None = None,
    rules: SMCRules = DEFAULT_RULES,
    *,
    with_breaks: bool = True,
) -> MTFContext:
    """Resample to each higher timeframe, analyse it, and align it back."""
    ltf_timeframe = str(frame["timeframe"].iloc[0]) if len(frame) else "M5"
    context = MTFContext(ltf_timeframe=ltf_timeframe)
    if frame.empty:
        return context

    ltf_minutes = get_timeframe(ltf_timeframe).minutes
    for timeframe in higher_timeframes or rules.mtf.higher_timeframes:
        tf = get_timeframe(timeframe)
        if tf.minutes <= ltf_minutes:
            continue
        htf_frame = resample_frame(frame, tf.name)
        if len(htf_frame) < 3:
            continue
        structure = build_structure(htf_frame, rules, with_breaks=with_breaks)
        mapping = align_htf(frame.index, ltf_timeframe, htf_frame.index, tf.name)
        context.views[tf.name] = MTFView(timeframe=tf.name, frame=htf_frame,
                                         structure=structure, mapping=mapping)
    return context
