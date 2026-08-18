"""Market structure: HH / HL / LH / LL, the protected levels, and bias.

Built entirely from swings that were CONFIRMED at or before the bar being
asked about, so the bias timeline inherits the swing engine's no-repaint
property (see tests/test_no_lookahead.py).

Scope note. Phase 3 derives bias from the *swing sequence* alone -- BOS,
CHOCH and MSS do not exist yet. `bias_source` records that, and Phase 4 will
add break-confirmed bias under the same field. Today's rule is deliberately
non-sticky: when the sequence stops being clean, the honest answer is RANGE,
not "still bullish because it was bullish yesterday".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import pandas as pd

from common.indicators import wilder_atr
from config.smc_rules import DEFAULT_RULES, SMCRules, StructureConfig
from structure.swings import SwingKind, SwingPoint, SwingSeries, detect_swings


class Bias(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    RANGE = "RANGE"


class BiasSource(str, Enum):
    SWING_SEQUENCE = "SWING_SEQUENCE"   # Phase 3
    NO_STRUCTURE = "NO_STRUCTURE"       # not enough confirmed swings yet


class StructureLabel(str, Enum):
    HH = "HH"
    HL = "HL"
    LH = "LH"
    LL = "LL"
    EQH = "EQH"                 # equal high within tolerance
    EQL = "EQL"
    FIRST_HIGH = "FIRST_HIGH"   # nothing to compare against yet
    FIRST_LOW = "FIRST_LOW"

    @property
    def is_bullish(self) -> bool:
        return self in (StructureLabel.HH, StructureLabel.HL)

    @property
    def is_bearish(self) -> bool:
        return self in (StructureLabel.LH, StructureLabel.LL)


class Scope(str, Enum):
    EXTERNAL = "EXTERNAL"
    INTERNAL = "INTERNAL"


@dataclass
class LabelledSwing:
    """A confirmed swing plus its label relative to the previous same-kind swing."""

    swing: SwingPoint
    label: StructureLabel
    compared_to_index: int | None      # formed_at_index of the swing compared against
    delta: float                       # price difference vs that swing (0 if none)

    @property
    def kind(self) -> SwingKind:
        return self.swing.kind

    @property
    def price(self) -> float:
        return self.swing.price

    @property
    def formed_at_index(self) -> int:
        return self.swing.formed_at_index

    @property
    def confirmed_at_index(self) -> int:
        return self.swing.confirmed_at_index


@dataclass
class StructureState:
    """The structural picture exactly as it stood at one bar."""

    index: int
    timestamp: pd.Timestamp | None
    bias: Bias
    bias_source: BiasSource
    structural_high: SwingPoint | None = None
    structural_low: SwingPoint | None = None
    protected_high: SwingPoint | None = None
    protected_low: SwingPoint | None = None
    last_high_label: StructureLabel | None = None
    last_low_label: StructureLabel | None = None
    range_width: float = float("nan")
    range_width_atr: float = float("nan")
    atr: float = float("nan")
    scope: Scope = Scope.EXTERNAL

    def describe(self) -> str:
        def level(swing: SwingPoint | None) -> str:
            return f"{swing.price:.5f} @ {swing.formed_at:%Y-%m-%d %H:%M}" if swing else "-"

        return "\n".join(
            [
                f"bias             : {self.bias.value} ({self.bias_source.value})",
                f"structural high  : {level(self.structural_high)}",
                f"structural low   : {level(self.structural_low)}",
                f"protected high   : {level(self.protected_high)}",
                f"protected low    : {level(self.protected_low)}",
                f"last labels      : high={self.last_high_label.value if self.last_high_label else '-'} "
                f"low={self.last_low_label.value if self.last_low_label else '-'}",
                f"dealing range    : {self.range_width:.5f} "
                f"({self.range_width_atr:.2f} x ATR)",
            ]
        )


@dataclass
class BiasChange:
    """A transition in the bias timeline, with the bar it became knowable."""

    index: int
    timestamp: pd.Timestamp
    previous: Bias
    current: Bias
    reason: str


@dataclass
class MarketStructure:
    """Labelled swings, a per-bar bias timeline, and time-travel accessors."""

    labels: list[LabelledSwing] = field(default_factory=list)
    bias_by_bar: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=object))
    changes: list[BiasChange] = field(default_factory=list)
    swings: SwingSeries = field(default_factory=SwingSeries)
    n_bars: int = 0
    symbol: str = ""
    timeframe: str = ""
    scope: Scope = Scope.EXTERNAL
    config: StructureConfig = field(default_factory=StructureConfig)
    internal: "MarketStructure | None" = None

    # -- accessors ---------------------------------------------------------

    def labels_known_at(self, index: int) -> list[LabelledSwing]:
        return [l for l in self.labels if l.confirmed_at_index <= index]

    def bias_at(self, index: int) -> Bias:
        if not self.n_bars:
            return Bias.RANGE
        return self.bias_by_bar[max(0, min(index, self.n_bars - 1))]

    def state_at(self, index: int, *, frame: pd.DataFrame | None = None,
                 atr: pd.Series | None = None) -> StructureState:
        """Recompute the full state at `index` from first principles.

        Deliberately independent of the forward pass that fills `bias_by_bar`,
        so the two can be cross-checked against each other in tests.
        """
        return _state_at(
            index,
            labels=self.labels,
            config=self.config,
            atr_value=float(atr.iloc[index]) if atr is not None else self._atr_cache(index),
            timestamp=self._timestamp(index, frame),
            scope=self.scope,
        )

    @property
    def current(self) -> StructureState:
        return self.state_at(self.n_bars - 1)

    def label_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.labels:
            counts[item.label.value] = counts.get(item.label.value, 0) + 1
        return counts

    def bias_share(self) -> dict[str, float]:
        """Fraction of bars spent in each bias -- a sanity check on parameters."""
        if not self.n_bars:
            return {}
        counts: dict[str, int] = {}
        for bias in self.bias_by_bar:
            counts[bias.value] = counts.get(bias.value, 0) + 1
        return {key: value / self.n_bars for key, value in sorted(counts.items())}

    def to_frame(self) -> pd.DataFrame:
        rows = [
            {
                "kind": l.kind.value,
                "label": l.label.value,
                "price": l.price,
                "formed_at": l.swing.formed_at,
                "formed_at_index": l.formed_at_index,
                "confirmed_at_index": l.confirmed_at_index,
                "delta": l.delta,
            }
            for l in self.labels
        ]
        return pd.DataFrame(rows)

    # -- internals ---------------------------------------------------------

    _atr_values: np.ndarray | None = None
    _timestamps: pd.DatetimeIndex | None = None

    def _atr_cache(self, index: int) -> float:
        if self._atr_values is None or not (0 <= index < len(self._atr_values)):
            return float("nan")
        return float(self._atr_values[index])

    def _timestamp(self, index: int, frame: pd.DataFrame | None) -> pd.Timestamp | None:
        source = frame.index if frame is not None else self._timestamps
        if source is None or not (0 <= index < len(source)):
            return None
        return source[index]


# ------------------------------------------------------------------ labelling

def _label_for(kind: SwingKind, price: float, previous: SwingPoint | None,
               tolerance: float) -> tuple[StructureLabel, float]:
    if previous is None:
        return (StructureLabel.FIRST_HIGH if kind is SwingKind.HIGH
                else StructureLabel.FIRST_LOW), 0.0

    delta = price - previous.price
    if abs(delta) <= tolerance:
        return (StructureLabel.EQH if kind is SwingKind.HIGH else StructureLabel.EQL), delta
    if kind is SwingKind.HIGH:
        return (StructureLabel.HH if delta > 0 else StructureLabel.LH), delta
    return (StructureLabel.HL if delta > 0 else StructureLabel.LL), delta


def _label_swings(swings: SwingSeries, config: StructureConfig,
                  atr_values: np.ndarray) -> list[LabelledSwing]:
    """Label every swing against the previous same-kind swing.

    Processed in confirmation order, and a swing that is about to be superseded
    is still the reference for the swing that supersedes it -- a higher high is
    an HH, which is exactly what a trader would have called it at the time.
    """
    ordered = sorted(swings.swings, key=lambda s: (s.confirmed_at_index, s.formed_at_index))
    previous: dict[SwingKind, SwingPoint | None] = {SwingKind.HIGH: None, SwingKind.LOW: None}
    labelled: list[LabelledSwing] = []

    for swing in ordered:
        atr_i = atr_values[swing.formed_at_index] if swing.formed_at_index < len(atr_values) else np.nan
        tolerance = (config.equal_tolerance_atr * atr_i) if np.isfinite(atr_i) else 0.0
        prev = previous[swing.kind]
        label, delta = _label_for(swing.kind, swing.price, prev, tolerance)

        labelled.append(
            LabelledSwing(
                swing=swing,
                label=label,
                compared_to_index=prev.formed_at_index if prev else None,
                delta=delta,
            )
        )
        previous[swing.kind] = swing

    return labelled


# --------------------------------------------------------------------- state

def _state_at(index: int, *, labels: list[LabelledSwing], config: StructureConfig,
              atr_value: float, timestamp: pd.Timestamp | None,
              scope: Scope) -> StructureState:
    known = [l for l in labels if l.confirmed_at_index <= index]

    last_high = next((l for l in reversed(known) if l.kind is SwingKind.HIGH), None)
    last_low = next((l for l in reversed(known) if l.kind is SwingKind.LOW), None)

    structural_high = last_high.swing if last_high else None
    structural_low = last_low.swing if last_low else None

    # The level that created the current one: the most recent opposite swing
    # that formed BEFORE it. Never a superseded swing -- supersession only
    # happens between consecutive same-kind swings, so one cannot sit here.
    protected_low = _preceding(known, SwingKind.LOW, structural_high)
    protected_high = _preceding(known, SwingKind.HIGH, structural_low)

    range_width = float("nan")
    range_width_atr = float("nan")
    if structural_high is not None and structural_low is not None:
        range_width = abs(structural_high.price - structural_low.price)
        if np.isfinite(atr_value) and atr_value > 0:
            range_width_atr = range_width / atr_value

    bias, source = _bias_from(
        last_high.label if last_high else None,
        last_low.label if last_low else None,
        range_width_atr,
        config,
    )

    return StructureState(
        index=index,
        timestamp=timestamp,
        bias=bias,
        bias_source=source,
        structural_high=structural_high,
        structural_low=structural_low,
        protected_high=protected_high,
        protected_low=protected_low,
        last_high_label=last_high.label if last_high else None,
        last_low_label=last_low.label if last_low else None,
        range_width=range_width,
        range_width_atr=range_width_atr,
        atr=atr_value,
        scope=scope,
    )


def _preceding(known: list[LabelledSwing], kind: SwingKind,
               reference: SwingPoint | None) -> SwingPoint | None:
    if reference is None:
        return None
    for item in reversed(known):
        if item.kind is kind and item.formed_at_index < reference.formed_at_index:
            return item.swing
    return None


def _bias_from(high_label: StructureLabel | None, low_label: StructureLabel | None,
               range_width_atr: float, config: StructureConfig) -> tuple[Bias, BiasSource]:
    if high_label is None or low_label is None:
        return Bias.RANGE, BiasSource.NO_STRUCTURE

    # A range too narrow to trade is a range whatever the labels say.
    if (config.range_atr_mult > 0 and np.isfinite(range_width_atr)
            and range_width_atr < config.range_atr_mult):
        return Bias.RANGE, BiasSource.SWING_SEQUENCE

    if high_label.is_bullish and low_label.is_bullish:
        return Bias.BULLISH, BiasSource.SWING_SEQUENCE
    if high_label.is_bearish and low_label.is_bearish:
        return Bias.BEARISH, BiasSource.SWING_SEQUENCE
    return Bias.RANGE, BiasSource.SWING_SEQUENCE


# ------------------------------------------------------------------ analysis

def analyze_structure(
    frame: pd.DataFrame,
    swings: SwingSeries,
    rules: SMCRules = DEFAULT_RULES,
    *,
    atr: pd.Series | None = None,
    scope: Scope = Scope.EXTERNAL,
) -> MarketStructure:
    """Label a swing series and build its per-bar bias timeline."""
    config = rules.structure
    n = len(frame)
    atr_series = atr if atr is not None else wilder_atr(frame, rules.atr_period)
    atr_values = atr_series.to_numpy(dtype="float64")

    labels = _label_swings(swings, config, atr_values)

    structure = MarketStructure(
        labels=labels,
        swings=swings,
        n_bars=n,
        symbol=swings.symbol,
        timeframe=swings.timeframe,
        scope=scope,
        config=config,
    )
    structure._atr_values = atr_values
    structure._timestamps = frame.index

    bias_by_bar = np.empty(n, dtype=object)
    changes: list[BiasChange] = []

    # One forward pass. `known` grows only as labels confirm, so bar t can
    # never see a swing that was not knowable at bar t.
    ordered = sorted(labels, key=lambda l: l.confirmed_at_index)
    cursor = 0
    known: list[LabelledSwing] = []
    last_high: LabelledSwing | None = None
    last_low: LabelledSwing | None = None
    previous_bias = Bias.RANGE

    for t in range(n):
        while cursor < len(ordered) and ordered[cursor].confirmed_at_index <= t:
            item = ordered[cursor]
            known.append(item)
            if item.kind is SwingKind.HIGH:
                last_high = item
            else:
                last_low = item
            cursor += 1

        structural_high = last_high.swing if last_high else None
        structural_low = last_low.swing if last_low else None
        range_width_atr = float("nan")
        if structural_high is not None and structural_low is not None:
            atr_t = atr_values[t]
            if np.isfinite(atr_t) and atr_t > 0:
                range_width_atr = abs(structural_high.price - structural_low.price) / atr_t

        bias, _source = _bias_from(
            last_high.label if last_high else None,
            last_low.label if last_low else None,
            range_width_atr,
            config,
        )
        bias_by_bar[t] = bias

        if t == 0:
            previous_bias = bias
        elif bias is not previous_bias:
            changes.append(
                BiasChange(
                    index=t,
                    timestamp=frame.index[t],
                    previous=previous_bias,
                    current=bias,
                    reason=(
                        f"high={last_high.label.value if last_high else '-'}, "
                        f"low={last_low.label.value if last_low else '-'}, "
                        f"range={range_width_atr:.2f}xATR"
                    ),
                )
            )
            previous_bias = bias

    structure.bias_by_bar = bias_by_bar
    structure.changes = changes
    return structure


def build_structure(
    frame: pd.DataFrame,
    rules: SMCRules = DEFAULT_RULES,
    *,
    atr: pd.Series | None = None,
) -> MarketStructure:
    """Convenience: detect external (and internal) swings and analyse both."""
    atr_series = atr if atr is not None else wilder_atr(frame, rules.atr_period)

    external_swings = detect_swings(frame, rules, atr=atr_series)
    external = analyze_structure(frame, external_swings, rules, atr=atr_series,
                                 scope=Scope.EXTERNAL)

    if rules.structure.track_internal:
        internal_rules = rules.model_copy(update={"swing": rules.internal_swing_config()})
        internal_swings = detect_swings(frame, internal_rules, atr=atr_series)
        external.internal = analyze_structure(frame, internal_swings, internal_rules,
                                              atr=atr_series, scope=Scope.INTERNAL)
    return external
