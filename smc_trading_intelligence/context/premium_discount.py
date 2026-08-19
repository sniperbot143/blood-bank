"""Dealing range, and where price sits inside it.

The dealing range runs from the confirmed structural low to the confirmed
structural high of the current leg (docs/SMC_DEFINITIONS.md §12). Inside it:

    0-50%    DISCOUNT      (cheap: where longs want to buy)
    ~50%     EQUILIBRIUM   (a configurable band around the midpoint)
    50-100%  PREMIUM       (expensive: where shorts want to sell)

Optionally an OTE sub-zone (0.62-0.79 retracement) is reported.

Everything comes from `MarketStructure.state_at(t)`, so the zone at bar `t` is
computed only from swings confirmed by `t` and inherits the no-repaint
property. A range narrower than `min_range_atr` is reported as
`NO_RANGE` rather than being split into meaningless percentages.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd

from common.indicators import wilder_atr
from config.smc_rules import DEFAULT_RULES, RangeConfig, SMCRules
from structure.market_structure import MarketStructure, build_structure


class Zone(str, Enum):
    DISCOUNT = "DISCOUNT"
    EQUILIBRIUM = "EQUILIBRIUM"
    PREMIUM = "PREMIUM"
    NO_RANGE = "NO_RANGE"       # not enough structure, or the range is noise


@dataclass(frozen=True)
class DealingRange:
    """The range as known at one bar, and where price sits in it."""

    index: int
    timestamp: pd.Timestamp | None
    high: float
    low: float
    price: float
    position: float             # 0.0 at the low, 1.0 at the high
    zone: Zone
    width: float
    width_atr: float
    in_ote: bool = False
    high_index: int | None = None
    low_index: int | None = None

    @property
    def equilibrium(self) -> float:
        return (self.high + self.low) / 2.0

    @property
    def is_valid(self) -> bool:
        return self.zone is not Zone.NO_RANGE

    def favours(self, bullish: bool) -> bool:
        """Discount favours longs, premium favours shorts."""
        if not self.is_valid:
            return False
        return self.zone is (Zone.DISCOUNT if bullish else Zone.PREMIUM)

    def as_dict(self) -> dict:
        return {
            "zone": self.zone.value,
            "position": self.position,
            "range_high": self.high,
            "range_low": self.low,
            "equilibrium": self.equilibrium,
            "width_atr": self.width_atr,
            "in_ote": self.in_ote,
        }


NO_RANGE = DealingRange(
    index=-1, timestamp=None, high=float("nan"), low=float("nan"),
    price=float("nan"), position=float("nan"), zone=Zone.NO_RANGE,
    width=float("nan"), width_atr=float("nan"),
)


def classify_position(position: float, config: RangeConfig) -> Zone:
    if not np.isfinite(position):
        return Zone.NO_RANGE
    if abs(position - 0.5) <= config.equilibrium_band:
        return Zone.EQUILIBRIUM
    return Zone.PREMIUM if position > 0.5 else Zone.DISCOUNT


def dealing_range_at(
    frame: pd.DataFrame,
    structure: MarketStructure,
    index: int,
    rules: SMCRules = DEFAULT_RULES,
    *,
    atr_value: float | None = None,
    price: float | None = None,
) -> DealingRange:
    """Where price sits in the current leg's range, as known at `index`."""
    config = rules.dealing_range
    if not (0 <= index < len(frame)):
        return NO_RANGE

    state = structure.state_at(index)
    if state.structural_high is None or state.structural_low is None:
        return NO_RANGE

    high = float(state.structural_high.price)
    low = float(state.structural_low.price)
    width = high - low
    if width <= 0:
        return NO_RANGE

    atr = atr_value if atr_value is not None else state.atr
    width_atr = width / atr if (atr and np.isfinite(atr) and atr > 0) else float("nan")
    if config.min_range_atr > 0 and np.isfinite(width_atr) and width_atr < config.min_range_atr:
        return DealingRange(
            index=index, timestamp=frame.index[index], high=high, low=low,
            price=float(frame["close"].iloc[index] if price is None else price),
            position=float("nan"), zone=Zone.NO_RANGE, width=width, width_atr=width_atr,
            high_index=state.structural_high.formed_at_index,
            low_index=state.structural_low.formed_at_index,
        )

    current = float(frame["close"].iloc[index]) if price is None else float(price)
    position = (current - low) / width

    # OTE is measured as a retracement from the extreme of the current leg:
    # for a range being retraced downward, 0.62-0.79 back from the high.
    retracement = 1.0 - position
    in_ote = bool(config.report_ote and config.ote_low <= retracement <= config.ote_high)

    return DealingRange(
        index=index, timestamp=frame.index[index], high=high, low=low, price=current,
        position=float(position), zone=classify_position(position, config),
        width=width, width_atr=width_atr, in_ote=in_ote,
        high_index=state.structural_high.formed_at_index,
        low_index=state.structural_low.formed_at_index,
    )


def dealing_range_series(
    frame: pd.DataFrame,
    structure: MarketStructure | None = None,
    rules: SMCRules = DEFAULT_RULES,
    *,
    atr: pd.Series | None = None,
) -> list[DealingRange]:
    """The dealing range at every bar. O(n) via the streamed level walk."""
    from structure.market_structure import iter_levels

    n = len(frame)
    if n == 0:
        return []
    atr_series = atr if atr is not None else wilder_atr(frame, rules.atr_period)
    structure = structure if structure is not None else build_structure(frame, rules, atr=atr_series)

    config = rules.dealing_range
    closes = frame["close"].to_numpy("float64")
    atr_values = atr_series.to_numpy("float64")
    out: list[DealingRange] = []

    for snapshot in iter_levels(structure):
        t = snapshot.index
        if snapshot.structural_high is None or snapshot.structural_low is None:
            out.append(NO_RANGE)
            continue

        high = float(snapshot.structural_high.price)
        low = float(snapshot.structural_low.price)
        width = high - low
        atr_t = atr_values[t]
        width_atr = width / atr_t if (np.isfinite(atr_t) and atr_t > 0) else float("nan")

        if width <= 0 or (config.min_range_atr > 0 and np.isfinite(width_atr)
                          and width_atr < config.min_range_atr):
            out.append(DealingRange(
                index=t, timestamp=frame.index[t], high=high, low=low,
                price=float(closes[t]), position=float("nan"), zone=Zone.NO_RANGE,
                width=width, width_atr=width_atr,
                high_index=snapshot.structural_high.formed_at_index,
                low_index=snapshot.structural_low.formed_at_index,
            ))
            continue

        position = (float(closes[t]) - low) / width
        retracement = 1.0 - position
        out.append(DealingRange(
            index=t, timestamp=frame.index[t], high=high, low=low, price=float(closes[t]),
            position=float(position), zone=classify_position(position, config),
            width=width, width_atr=width_atr,
            in_ote=bool(config.report_ote and config.ote_low <= retracement <= config.ote_high),
            high_index=snapshot.structural_high.formed_at_index,
            low_index=snapshot.structural_low.formed_at_index,
        ))
    return out


def zone_share(ranges: list[DealingRange]) -> dict[str, float]:
    """Fraction of bars in each zone -- a sanity check on the range settings."""
    if not ranges:
        return {}
    counts: dict[str, int] = {}
    for item in ranges:
        counts[item.zone.value] = counts.get(item.zone.value, 0) + 1
    return {k: v / len(ranges) for k, v in sorted(counts.items())}
