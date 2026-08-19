"""Market regime: the similarity key probabilities are grouped by.

Two axes (docs/SMC_DEFINITIONS.md §14), both from closed data only:

    volatility   ATR percentile within its own trailing window -> LOW/NORMAL/HIGH
    trend        ADX -> RANGE / WEAK_TREND / TREND, signed by structure bias

`regime_key` is the pair, e.g. "TREND_DOWN|HIGH_VOL". It is a *label for
grouping*, not a filter: probabilities are estimated within a regime rather
than trades being blocked by one.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd

from common.indicators import directional_movement, rolling_percentile, wilder_atr
from config.smc_rules import DEFAULT_RULES, RegimeConfig, SMCRules
from structure.market_structure import Bias, MarketStructure


class VolatilityRegime(str, Enum):
    LOW = "LOW_VOL"
    NORMAL = "NORMAL_VOL"
    HIGH = "HIGH_VOL"
    UNKNOWN = "UNKNOWN_VOL"


class TrendRegime(str, Enum):
    RANGE = "RANGE"
    WEAK_TREND_UP = "WEAK_TREND_UP"
    WEAK_TREND_DOWN = "WEAK_TREND_DOWN"
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    UNKNOWN = "UNKNOWN_TREND"


@dataclass(frozen=True)
class Regime:
    index: int
    volatility: VolatilityRegime
    trend: TrendRegime
    atr: float
    atr_percentile: float
    adx: float

    @property
    def key(self) -> str:
        return f"{self.trend.value}|{self.volatility.value}"

    @property
    def is_known(self) -> bool:
        return (self.volatility is not VolatilityRegime.UNKNOWN
                and self.trend is not TrendRegime.UNKNOWN)

    def as_dict(self) -> dict:
        return {
            "regime_key": self.key,
            "volatility_regime": self.volatility.value,
            "trend_regime": self.trend.value,
            "atr": self.atr,
            "atr_percentile": self.atr_percentile,
            "adx": self.adx,
        }


@dataclass
class RegimeSeries:
    regimes: list[Regime]
    n_bars: int = 0

    def at(self, index: int) -> Regime:
        if not self.regimes:
            return Regime(index, VolatilityRegime.UNKNOWN, TrendRegime.UNKNOWN,
                          float("nan"), float("nan"), float("nan"))
        return self.regimes[max(0, min(index, len(self.regimes) - 1))]

    def key_at(self, index: int) -> str:
        return self.at(index).key

    def share(self) -> dict[str, float]:
        if not self.regimes:
            return {}
        counts: dict[str, int] = {}
        for regime in self.regimes:
            counts[regime.key] = counts.get(regime.key, 0) + 1
        return {k: v / len(self.regimes) for k, v in sorted(counts.items())}


def build_regimes(
    frame: pd.DataFrame,
    structure: MarketStructure | None = None,
    rules: SMCRules = DEFAULT_RULES,
    *,
    atr: pd.Series | None = None,
) -> RegimeSeries:
    """Label every bar with its volatility and trend regime."""
    config = rules.regime
    n = len(frame)
    if n == 0:
        return RegimeSeries(regimes=[], n_bars=0)

    atr_series = atr if atr is not None else wilder_atr(frame, rules.atr_period)
    atr_values = atr_series.to_numpy("float64")
    percentiles = rolling_percentile(atr_series, config.lookback).to_numpy("float64")
    adx = directional_movement(frame, config.adx_period)["adx"].to_numpy("float64")

    regimes: list[Regime] = []
    for t in range(n):
        pct = percentiles[t]
        if not np.isfinite(pct):
            volatility = VolatilityRegime.UNKNOWN
        elif pct < config.low_vol_percentile:
            volatility = VolatilityRegime.LOW
        elif pct > config.high_vol_percentile:
            volatility = VolatilityRegime.HIGH
        else:
            volatility = VolatilityRegime.NORMAL

        adx_t = adx[t]
        if not np.isfinite(adx_t):
            trend = TrendRegime.UNKNOWN
        elif adx_t < config.range_adx:
            trend = TrendRegime.RANGE
        else:
            bias = structure.bias_at(t) if structure is not None else Bias.RANGE
            strong = adx_t >= config.trend_adx
            if bias is Bias.BULLISH:
                trend = TrendRegime.TREND_UP if strong else TrendRegime.WEAK_TREND_UP
            elif bias is Bias.BEARISH:
                trend = TrendRegime.TREND_DOWN if strong else TrendRegime.WEAK_TREND_DOWN
            else:
                trend = TrendRegime.RANGE

        regimes.append(Regime(index=t, volatility=volatility, trend=trend,
                              atr=float(atr_values[t]), atr_percentile=float(pct),
                              adx=float(adx_t)))
    return RegimeSeries(regimes=regimes, n_bars=n)
