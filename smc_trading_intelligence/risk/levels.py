"""Where the entry, the stop and the targets go.

Stops come from structure (the sweep extreme, the far edge of the point of
interest) with an ATR buffer, never from a fixed pip count. Targets prefer
real liquidity -- the pools price is actually reaching for -- and fall back to
R multiples when no pool is in range.

Nothing here reads a bar after the signal bar.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from config.smc_rules import SetupConfig
from features.context import MarketContext


@dataclass(frozen=True)
class TradeLevels:
    """A complete trade geometry, or a reason why there isn't one."""

    entry: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    take_profit_3: float
    risk: float
    risk_atr: float
    rr1: float
    rr2: float
    rr3: float
    tp1_source: str
    stop_source: str
    invalid_reason: str | None = None

    @property
    def is_valid(self) -> bool:
        return self.invalid_reason is None

    def as_dict(self) -> dict:
        return {
            "entry": self.entry, "stop_loss": self.stop_loss,
            "take_profit_1": self.take_profit_1, "take_profit_2": self.take_profit_2,
            "take_profit_3": self.take_profit_3, "risk": self.risk,
            "risk_atr": self.risk_atr, "risk_reward": self.rr1, "rr2": self.rr2,
            "rr3": self.rr3, "tp1_source": self.tp1_source, "stop_source": self.stop_source,
        }


def _invalid(reason: str) -> TradeLevels:
    nan = float("nan")
    return TradeLevels(nan, nan, nan, nan, nan, nan, nan, nan, nan, nan,
                       "NONE", "NONE", invalid_reason=reason)


def build_levels(
    context: MarketContext,
    index: int,
    *,
    bullish: bool,
    entry: float,
    protective_price: float,
    stop_source: str,
    config: SetupConfig,
) -> TradeLevels:
    """Turn an entry and a structural invalidation price into a full trade.

    `protective_price` is the level that must not be lost -- the sweep extreme
    or the far edge of the order block. The stop sits beyond it by a buffer.
    """
    atr = context.atr_at(index)
    if not np.isfinite(atr) or atr <= 0:
        return _invalid("NO_ATR")
    if not np.isfinite(entry) or not np.isfinite(protective_price):
        return _invalid("NO_PRICE")

    buffer = config.stop_buffer_atr * atr
    stop = protective_price - buffer if bullish else protective_price + buffer
    risk = (entry - stop) if bullish else (stop - entry)
    if risk <= 0:
        return _invalid("STOP_ON_WRONG_SIDE")

    risk_atr = risk / atr
    if risk_atr < config.min_stop_atr:
        return _invalid("STOP_TOO_TIGHT")
    if risk_atr > config.max_stop_atr:
        return _invalid("STOP_TOO_WIDE")

    # TP1 prefers the liquidity price is reaching for.
    tp1 = entry + config.tp1_rr * risk if bullish else entry - config.tp1_rr * risk
    tp1_source = "RR"
    if config.prefer_liquidity_targets:
        pool = (context.liquidity.nearest_above(entry, index) if bullish
                else context.liquidity.nearest_below(entry, index))
        if pool is not None:
            level = pool.price_at(index)
            reward = (level - entry) if bullish else (entry - level)
            if reward / risk >= config.min_rr:
                tp1, tp1_source = level, pool.kind.value

    tp2 = entry + config.tp2_rr * risk if bullish else entry - config.tp2_rr * risk
    tp3 = entry + config.tp3_rr * risk if bullish else entry - config.tp3_rr * risk

    def _rr(target: float) -> float:
        reward = (target - entry) if bullish else (entry - target)
        return float(reward / risk)

    rr1 = _rr(tp1)
    if rr1 < config.min_rr:
        return _invalid("RR_TOO_LOW")

    return TradeLevels(
        entry=float(entry), stop_loss=float(stop),
        take_profit_1=float(tp1), take_profit_2=float(tp2), take_profit_3=float(tp3),
        risk=float(risk), risk_atr=float(risk_atr),
        rr1=rr1, rr2=_rr(tp2), rr3=_rr(tp3),
        tp1_source=tp1_source, stop_source=stop_source,
    )


def position_size(
    account_balance: float,
    risk_percent: float,
    risk_price: float,
    *,
    contract_size: float = 1.0,
    volume_step: float = 0.01,
    volume_min: float = 0.01,
    volume_max: float = 100.0,
    value_per_unit: float = 1.0,
) -> float:
    """Lots for a given account risk. Rounded DOWN to the broker's step.

    Rounding down matters: rounding up silently risks more than the user asked.
    """
    if risk_price <= 0 or account_balance <= 0 or risk_percent <= 0:
        return 0.0
    risk_money = account_balance * (risk_percent / 100.0)
    per_lot = risk_price * contract_size * value_per_unit
    if per_lot <= 0:
        return 0.0

    raw = risk_money / per_lot
    stepped = np.floor(raw / volume_step) * volume_step
    if stepped < volume_min:
        return 0.0
    return float(min(round(stepped, 8), volume_max))
