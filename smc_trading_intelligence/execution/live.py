"""Live execution. DISABLED BY DEFAULT, and gated twice on purpose.

This is the only module in the project that can lose money. It therefore
requires, all at once:

    1. ENABLE_LIVE=true in the environment
    2. `confirm="I UNDERSTAND THIS PLACES REAL ORDERS"` passed in code
    3. a preflight check that passes (see `preflight()`)

Missing any one of them raises. There is no "just this once" flag, no
`force=True`, and no default that trades.

The preconditions in ARCHITECTURE.md §9 are not enforceable in software -- a
green walk-forward, 200+ paper trades matching backtest assumptions,
calibration error under target -- so `preflight()` reports what it CAN check
and names what only you can confirm.

Nothing in the analysis pipeline imports this file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd

from execution.broker import Broker, CloseReason, OrderState, Position

CONFIRMATION_PHRASE = "I UNDERSTAND THIS PLACES REAL ORDERS"


class LiveTradingDisabled(RuntimeError):
    """Raised whenever live trading is attempted without every gate open."""


@dataclass
class PreflightReport:
    """What could be checked automatically, and what could not."""

    checks: dict[str, bool] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    manual: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(self.checks.values())

    def summary(self) -> str:
        lines = ["preflight:"]
        for name, ok in self.checks.items():
            lines.append(f"  [{'ok' if ok else 'FAIL'}] {name}")
        for warning in self.warnings:
            lines.append(f"  [warn] {warning}")
        if self.manual:
            lines.append("  only you can confirm these:")
            lines += [f"    - {item}" for item in self.manual]
        return "\n".join(lines)


def is_enabled() -> bool:
    return os.getenv("ENABLE_LIVE", "false").strip().lower() in {"1", "true", "yes", "on"}


def preflight(settings=None) -> PreflightReport:
    """Check what a machine can check before real orders are possible."""
    from config.settings import get_settings
    from data.mt5_connector import MT5_AVAILABLE

    settings = settings or get_settings()
    report = PreflightReport()
    report.checks["ENABLE_LIVE is set"] = is_enabled()
    report.checks["MetaTrader5 package importable"] = MT5_AVAILABLE
    report.checks["setup database exists"] = settings.db_path.exists()

    if not settings.mt5_login:
        report.warnings.append("no MT5_LOGIN in .env -- relying on an already logged-in terminal")
    if settings.broker_utc_offset_hours == 0:
        report.warnings.append("BROKER_UTC_OFFSET is 0 -- verify that against your terminal")

    report.manual = [
        "walk-forward is green out-of-sample on this symbol and timeframe",
        "at least 200 paper trades, with fills matching backtest assumptions",
        "calibration error under target (probability engine beats the base rate)",
        "risk per trade, daily loss limit and max positions are set deliberately",
    ]
    return report


@dataclass
class LiveBroker(Broker):
    """MT5 order placement. Every method refuses unless all gates are open.

    Construction alone does nothing: the gates are checked at construction AND
    again on every order, so flipping the environment variable mid-run does not
    silently arm an already-built object.
    """

    symbol: str
    magic: int = 20240819
    max_positions: int = 1
    max_daily_loss: float = 0.0        # account currency; 0 disables the check
    deviation_points: int = 20
    confirm: str = ""
    _armed: bool = False
    _positions: list[Position] = field(default_factory=list)
    _realised_today: float = 0.0
    _day: str = ""

    def __post_init__(self) -> None:
        self._check_gates()
        self._armed = True

    # -- gates -------------------------------------------------------------

    def _check_gates(self) -> None:
        if not is_enabled():
            raise LiveTradingDisabled(
                "live trading is disabled. Set ENABLE_LIVE=true in .env only when "
                "you have read execution/live.py and run preflight()."
            )
        if self.confirm != CONFIRMATION_PHRASE:
            raise LiveTradingDisabled(
                f'live trading requires confirm="{CONFIRMATION_PHRASE}" -- '
                "passing it is a deliberate act, which is the point."
            )
        report = preflight()
        if not report.passed:
            raise LiveTradingDisabled("preflight failed:\n" + report.summary())

    def _guard_daily_loss(self) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._day:
            self._day, self._realised_today = today, 0.0
        if self.max_daily_loss > 0 and self._realised_today <= -abs(self.max_daily_loss):
            raise LiveTradingDisabled(
                f"daily loss limit hit ({self._realised_today:.2f}); trading halted for today"
            )

    # -- Broker interface --------------------------------------------------

    def place(self, signal, *, volume: float = 0.01, expires_in_bars: int = 12) -> Position:
        self._check_gates()
        self._guard_daily_loss()
        if len(self.open_positions()) >= self.max_positions:
            raise LiveTradingDisabled(
                f"max_positions={self.max_positions} already open; refusing to add"
            )

        import MetaTrader5 as mt5  # imported here so the module loads anywhere

        candidate = signal.candidate
        levels = candidate.levels
        request = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": self.symbol,
            "volume": float(volume),
            "type": (mt5.ORDER_TYPE_BUY_LIMIT if candidate.bullish
                     else mt5.ORDER_TYPE_SELL_LIMIT),
            "price": float(levels.entry),
            "sl": float(levels.stop_loss),
            "tp": float(levels.take_profit_1),
            "deviation": self.deviation_points,
            "magic": self.magic,
            "comment": candidate.setup_type[:31],
            "type_time": mt5.ORDER_TIME_GTC,
        }
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            code = getattr(result, "retcode", "no response")
            raise RuntimeError(f"order_send failed: {code}")

        position = Position(
            ticket=int(result.order), symbol=self.symbol, timeframe=candidate.timeframe,
            direction=candidate.direction, volume=float(volume), entry=levels.entry,
            stop_loss=levels.stop_loss, take_profit=levels.take_profit_1,
            take_profit_2=levels.take_profit_2, state=OrderState.PENDING,
            signal_time=candidate.signal_time, setup_type=candidate.setup_type,
            decision=signal.decision.value, probability=signal.probability.probability,
            score=signal.score.total,
        )
        self._positions.append(position)
        return position

    def on_bar(self, bar: pd.Series, index: int) -> list[Position]:
        """Live positions are managed by the broker, so this reconciles state."""
        self._check_gates()
        return self.reconcile()

    def reconcile(self) -> list[Position]:
        """Re-read the terminal after a restart -- never trust local memory."""
        self._check_gates()
        import MetaTrader5 as mt5

        live = mt5.positions_get(symbol=self.symbol) or ()
        tickets = {int(p.ticket) for p in live}
        for position in self._positions:
            if position.state is OrderState.OPEN and position.ticket not in tickets:
                position.state = OrderState.CLOSED
                position.close_reason = CloseReason.MANUAL
                position.comment = "closed outside this process"
        return self._positions

    def open_positions(self) -> list[Position]:
        return [p for p in self._positions if p.state is OrderState.OPEN]

    def close(self, ticket: int, price: float, reason: CloseReason = CloseReason.MANUAL,
              when: pd.Timestamp | None = None) -> Position | None:
        self._check_gates()
        import MetaTrader5 as mt5

        for position in self._positions:
            if position.ticket != ticket or not position.is_live:
                continue
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": self.symbol,
                "volume": position.volume,
                "type": (mt5.ORDER_TYPE_SELL if position.bullish else mt5.ORDER_TYPE_BUY),
                "position": ticket,
                "deviation": self.deviation_points,
                "magic": self.magic,
            }
            result = mt5.order_send(request)
            if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                raise RuntimeError(f"close failed: {getattr(result, 'retcode', 'no response')}")
            position.state = OrderState.CLOSED
            position.close_reason = reason
            position.closed_at = when
            return position
        return None
