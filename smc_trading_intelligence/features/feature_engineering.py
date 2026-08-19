"""The feature vector: what gets stored, grouped by, and eventually modelled.

Every setup written to the database carries this dict (docs/SMC_DEFINITIONS.md
§25). Two rules govern it:

  1. **As-of only.** Every value comes from `MarketContext.at(t)` or from
     objects whose `confirmed_at_index <= t`. Nothing is computed from a bar
     after the signal.
  2. **Missing means missing.** An absent order block gives `ob_size_atr =
     NaN`, never 0.0. A zero would be read as "a zero-sized block existed",
     which is a different and false claim.

Categorical features are strings so they can be used as exact-match keys in
the probability engine's similarity tiers; continuous ones are ATR-normalised
so they mean the same thing across instruments.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from features.context import MarketContext, Snapshot
from imbalance.fvg import FVGDirection
from liquidity.levels import Side
from orderblocks.order_blocks import OBDirection
from structure.breaks import BreakType

NA = float("nan")

# The keys the setup database indexes on, as opposed to the JSON blob.
CATEGORICAL_KEYS = [
    "direction", "bias", "htf_bias_agreement", "regime_key", "session",
    "pd_zone", "structure_event", "liquidity_event", "poi_type",
]


def _safe_div(numerator: float, denominator: float) -> float:
    if denominator is None or not np.isfinite(denominator) or denominator == 0:
        return NA
    return float(numerator / denominator)


@dataclass(frozen=True)
class FeatureSet:
    """A feature vector plus the keys used for similarity grouping."""

    values: dict[str, float | str | int | None]

    def get(self, key: str, default=NA):
        return self.values.get(key, default)

    def categorical(self) -> dict[str, str]:
        return {k: str(self.values.get(k, "")) for k in CATEGORICAL_KEYS}

    def numeric(self) -> dict[str, float]:
        return {k: float(v) for k, v in self.values.items()
                if isinstance(v, (int, float)) and not isinstance(v, bool)}

    def missing(self) -> list[str]:
        return [k for k, v in self.values.items()
                if v is None or (isinstance(v, float) and math.isnan(v))]


def extract_features(
    context: MarketContext,
    index: int,
    *,
    direction_bullish: bool,
    snapshot: Snapshot | None = None,
    entry: float | None = None,
    stop_loss: float | None = None,
    take_profit: float | None = None,
) -> FeatureSet:
    """Build the feature vector for a candidate setup at bar `index`."""
    snap = snapshot if snapshot is not None else context.at(index)
    atr = snap.atr
    price = entry if entry is not None else snap.close
    direction = "BUY" if direction_bullish else "SELL"

    values: dict[str, float | str | int | None] = {
        # -- identity ------------------------------------------------------
        "symbol": context.symbol,
        "timeframe": context.timeframe,
        "index": index,
        "direction": direction,
        "atr": atr,
        # -- context -------------------------------------------------------
        "bias": snap.bias.value,
        "regime_key": snap.regime.key,
        "trend_regime": snap.regime.trend.value,
        "volatility_regime": snap.regime.volatility.value,
        "adx": snap.regime.adx,
        "atr_percentile": snap.regime.atr_percentile,
        "session": snap.session or "NONE",
        "hour_of_day": int(snap.timestamp.hour),
        "day_of_week": int(snap.timestamp.dayofweek),
        # -- premium / discount -------------------------------------------
        "pd_zone": snap.dealing_range.zone.value,
        "pd_position": snap.dealing_range.position if snap.dealing_range.is_valid else NA,
        "range_width_atr": snap.dealing_range.width_atr,
        "in_ote": int(bool(snap.dealing_range.in_ote)),
        # -- spread / cost proxy ------------------------------------------
        "spread_points": float(context.frame["spread"].iloc[index]),
        "tick_volume": float(context.frame["tick_volume"].iloc[index]),
    }

    # -- multi-timeframe ---------------------------------------------------
    wanted = "BULLISH" if direction_bullish else "BEARISH"
    htf = snap.htf_bias
    agreeing = sum(1 for v in htf.values() if v == wanted)
    opposing = sum(1 for v in htf.values() if v not in (wanted, "RANGE"))
    values.update({
        "htf_count": len(htf),
        "htf_agreeing": agreeing,
        "htf_opposing": opposing,
        "htf_bias_agreement": ("ALIGNED" if htf and agreeing == len(htf)
                               else "OPPOSED" if opposing else "MIXED"),
    })
    for timeframe, bias in htf.items():
        values[f"htf_{timeframe}_bias"] = bias

    # -- structure event ---------------------------------------------------
    event = snap.last_break
    values.update({
        "structure_event": (f"{event.type.value}_{event.direction.value}"
                            if event is not None else "NONE"),
        "structure_event_age": (index - event.index) if event is not None else NA,
        "structure_aligned": int(bool(
            event is not None and (event.direction.value == "BULLISH") == direction_bullish)),
        "displacement_score": event.displacement.score if event is not None else NA,
        "displacement_bars": event.displacement.bars if event is not None else NA,
        "mss_present": int(bool(event is not None and event.type is BreakType.MSS)),
        "distance_to_structural_high_atr": _safe_div(
            (snap.structure.structural_high.price - price) if snap.structure.structural_high else NA,
            atr),
        "distance_to_structural_low_atr": _safe_div(
            (price - snap.structure.structural_low.price) if snap.structure.structural_low else NA,
            atr),
    })

    # -- liquidity ---------------------------------------------------------
    sweep = snap.last_sweep
    wanted_sweep = Side.SELL_SIDE if direction_bullish else Side.BUY_SIDE
    values.update({
        "liquidity_event": sweep.type.value if sweep is not None else "NONE",
        "sweep_age": (index - sweep.confirmed_at_index) if sweep is not None else NA,
        "sweep_magnitude_atr": sweep.magnitude_atr if sweep is not None else NA,
        "sweep_rejection_atr": sweep.rejection_atr if sweep is not None else NA,
        "sweep_close_location": sweep.close_location if sweep is not None else NA,
        "sweep_bars_to_reject": sweep.bars_to_reject if sweep is not None else NA,
        "sweep_pool_kind": sweep.pool_kind.value if sweep is not None else "NONE",
        "sweep_pool_strength": sweep.pool_strength if sweep is not None else NA,
        "sweep_volume_ratio": sweep.volume_ratio if sweep is not None else NA,
        "sweep_aligned": int(bool(
            sweep is not None and sweep.direction_bullish == direction_bullish)),
    })

    target_pool = (context.liquidity.nearest_above(price, index) if direction_bullish
                   else context.liquidity.nearest_below(price, index))
    opposing_pool = (context.liquidity.nearest_below(price, index) if direction_bullish
                     else context.liquidity.nearest_above(price, index))
    values.update({
        "target_liquidity_atr": _safe_div(
            abs(target_pool.price_at(index) - price) if target_pool else NA, atr),
        "target_liquidity_kind": target_pool.kind.value if target_pool else "NONE",
        "target_liquidity_strength": target_pool.strength_at(index) if target_pool else NA,
        "opposing_liquidity_atr": _safe_div(
            abs(opposing_pool.price_at(index) - price) if opposing_pool else NA, atr),
        "intact_pool_count": len(snap.intact_pools),
        "_wanted_sweep_side": wanted_sweep.value,
    })

    # -- points of interest ------------------------------------------------
    ob_direction = OBDirection.BULLISH if direction_bullish else OBDirection.BEARISH
    block = context.order_blocks.nearest(price, index, ob_direction)
    values.update({
        "ob_present": int(block is not None),
        "ob_size_atr": block.size_atr if block else NA,
        "ob_distance_atr": _safe_div(abs(block.mid - price) if block else NA, atr),
        "ob_age": block.age_at(index) if block else NA,
        "ob_fill": block.fill_at(index) if block else NA,
        "ob_fresh": int(bool(block and block.is_fresh_at(index))),
        "ob_displacement": block.displacement_score if block else NA,
    })

    fvg_direction = FVGDirection.BULLISH if direction_bullish else FVGDirection.BEARISH
    gap = context.fvgs.nearest(price, index, fvg_direction)
    values.update({
        "fvg_present": int(gap is not None),
        "fvg_size_atr": gap.size_atr if gap else NA,
        "fvg_distance_atr": _safe_div(abs(gap.mid - price) if gap else NA, atr),
        "fvg_fill": gap.fill_at(index) if gap else NA,
        "fvg_age": gap.age_at(index) if gap else NA,
        "ifvg_present": int(context.ifvgs.nearest(price, index, fvg_direction) is not None),
    })

    overlapping = bool(block and gap and not (gap.top < block.bottom or gap.bottom > block.top))
    values["poi_type"] = ("OB_FVG" if overlapping else "OB" if block
                          else "FVG" if gap else "NONE")

    # -- trade geometry ----------------------------------------------------
    if entry is not None and stop_loss is not None:
        risk = abs(entry - stop_loss)
        values["risk_atr"] = _safe_div(risk, atr)
        values["risk_reward"] = _safe_div(abs((take_profit or NA) - entry), risk) \
            if take_profit is not None else NA
    else:
        values["risk_atr"] = NA
        values["risk_reward"] = NA

    # -- confluence count (inputs to the Phase 15 score) -------------------
    values["confluence_count"] = int(sum([
        bool(values["structure_aligned"]),
        bool(values["sweep_aligned"]),
        bool(values["ob_present"]),
        bool(values["fvg_present"]),
        values["htf_bias_agreement"] == "ALIGNED",
        snap.dealing_range.favours(direction_bullish),
        bool(values["mss_present"]),
    ]))

    return FeatureSet(values=values)


def feature_names() -> list[str]:
    """Stable ordering for anything that needs a fixed schema."""
    return sorted({
        "symbol", "timeframe", "index", "direction", "atr", "bias", "regime_key",
        "trend_regime", "volatility_regime", "adx", "atr_percentile", "session",
        "hour_of_day", "day_of_week", "pd_zone", "pd_position", "range_width_atr",
        "in_ote", "spread_points", "tick_volume", "htf_count", "htf_agreeing",
        "htf_opposing", "htf_bias_agreement", "structure_event", "structure_event_age",
        "structure_aligned", "displacement_score", "displacement_bars", "mss_present",
        "distance_to_structural_high_atr", "distance_to_structural_low_atr",
        "liquidity_event", "sweep_age", "sweep_magnitude_atr", "sweep_rejection_atr",
        "sweep_close_location", "sweep_bars_to_reject", "sweep_pool_kind",
        "sweep_pool_strength", "sweep_volume_ratio", "sweep_aligned",
        "target_liquidity_atr", "target_liquidity_kind", "target_liquidity_strength",
        "opposing_liquidity_atr", "intact_pool_count", "ob_present", "ob_size_atr",
        "ob_distance_atr", "ob_age", "ob_fill", "ob_fresh", "ob_displacement",
        "fvg_present", "fvg_size_atr", "fvg_distance_atr", "fvg_fill", "fvg_age",
        "ifvg_present", "poi_type", "risk_atr", "risk_reward", "confluence_count",
    })
