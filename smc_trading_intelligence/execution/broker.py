"""The broker interface, and the paper implementation of it.

Signal generation never imports this module. A runner takes signals from the
engine and hands them to a Broker; that separation is why `execution/` can be
deleted from disk and the analysis system still runs (ARCHITECTURE.md §9).

`PaperBroker` behaves the way the live one will: same interface, same order
lifecycle, same journal. Anything the paper engine cannot do -- partial fills,
requotes, weekend gaps against a stop -- is a difference to find BEFORE money
is involved, not after.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

import numpy as np
import pandas as pd


class OrderState(str, Enum):
    PENDING = "PENDING"        # a limit waiting to be filled
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class CloseReason(str, Enum):
    TAKE_PROFIT = "TAKE_PROFIT"
    STOP_LOSS = "STOP_LOSS"
    TIMEOUT = "TIMEOUT"
    MANUAL = "MANUAL"
    EXPIRED = "EXPIRED"


@dataclass
class Position:
    """One order through its whole life, in one record."""

    ticket: int
    symbol: str
    timeframe: str
    direction: str
    volume: float
    entry: float
    stop_loss: float
    take_profit: float
    take_profit_2: float = float("nan")
    state: OrderState = OrderState.PENDING
    opened_at: pd.Timestamp | None = None
    closed_at: pd.Timestamp | None = None
    fill_price: float = float("nan")
    exit_price: float = float("nan")
    close_reason: CloseReason | None = None
    r_multiple: float = float("nan")
    profit: float = float("nan")
    signal_time: pd.Timestamp | None = None
    setup_type: str = ""
    decision: str = ""
    probability: float = float("nan")
    score: float = float("nan")
    expires_at_index: int | None = None
    comment: str = ""

    @property
    def is_live(self) -> bool:
        return self.state in (OrderState.PENDING, OrderState.OPEN)

    @property
    def bullish(self) -> bool:
        return self.direction.upper() in ("BUY", "LONG", "BULLISH")

    @property
    def risk(self) -> float:
        return abs(self.entry - self.stop_loss)

    def as_dict(self) -> dict:
        data = asdict(self)
        data["state"] = self.state.value
        data["close_reason"] = self.close_reason.value if self.close_reason else None
        for key in ("opened_at", "closed_at", "signal_time"):
            value = data.get(key)
            data[key] = value.isoformat() if isinstance(value, pd.Timestamp) else value
        return data


class Broker(ABC):
    """What a runner is allowed to ask for. Deliberately small."""

    @abstractmethod
    def place(self, signal, *, volume: float, expires_in_bars: int) -> Position: ...

    @abstractmethod
    def on_bar(self, bar: pd.Series, index: int) -> list[Position]: ...

    @abstractmethod
    def open_positions(self) -> list[Position]: ...

    @abstractmethod
    def close(self, ticket: int, price: float, reason: CloseReason,
              when: pd.Timestamp | None = None) -> Position | None: ...


@dataclass
class PaperBroker(Broker):
    """Simulated fills against closed bars, with the same rules as labelling.

    Pessimistic on purpose: a bar that touches both the target and the stop is
    recorded as a stop, because the true intrabar order is unknown.
    """

    balance: float = 10_000.0
    starting_balance: float = 10_000.0
    spread_cost: float = 0.0
    slippage: float = 0.0
    positions: list[Position] = field(default_factory=list)
    journal_path: Path | None = None
    _next_ticket: int = 1

    # -- orders ------------------------------------------------------------

    def place(self, signal, *, volume: float = 1.0, expires_in_bars: int = 12) -> Position:
        candidate = signal.candidate
        levels = candidate.levels
        position = Position(
            ticket=self._next_ticket, symbol=candidate.symbol, timeframe=candidate.timeframe,
            direction=candidate.direction, volume=volume, entry=levels.entry,
            stop_loss=levels.stop_loss, take_profit=levels.take_profit_1,
            take_profit_2=levels.take_profit_2, signal_time=candidate.signal_time,
            setup_type=candidate.setup_type, decision=signal.decision.value,
            probability=signal.probability.probability, score=signal.score.total,
            expires_at_index=candidate.signal_index + expires_in_bars,
        )
        self._next_ticket += 1
        self.positions.append(position)
        self._journal("PLACED", position)
        return position

    def on_bar(self, bar: pd.Series, index: int) -> list[Position]:
        """Advance every live order by one CLOSED bar. Returns what changed."""
        changed: list[Position] = []
        high, low = float(bar["high"]), float(bar["low"])
        when = bar.name if isinstance(bar.name, pd.Timestamp) else None

        for position in self.positions:
            if not position.is_live:
                continue

            if position.state is OrderState.PENDING:
                if position.expires_at_index is not None and index > position.expires_at_index:
                    position.state = OrderState.CANCELLED
                    position.close_reason = CloseReason.EXPIRED
                    position.closed_at = when
                    changed.append(position)
                    self._journal("EXPIRED", position)
                    continue
                reached = low <= position.entry if position.bullish else high >= position.entry
                if reached:
                    slip = self.spread_cost + self.slippage
                    position.fill_price = (position.entry + slip if position.bullish
                                           else position.entry - slip)
                    position.state = OrderState.OPEN
                    position.opened_at = when
                    changed.append(position)
                    self._journal("FILLED", position)
                continue

            hit_stop = low <= position.stop_loss if position.bullish else high >= position.stop_loss
            hit_target = (high >= position.take_profit if position.bullish
                          else low <= position.take_profit)

            if hit_stop and hit_target:
                # Unknowable order inside one bar -- assume the worse side.
                self._settle(position, position.stop_loss, CloseReason.STOP_LOSS, when)
                position.comment = "ambiguous bar: stop assumed first"
                changed.append(position)
            elif hit_stop:
                self._settle(position, position.stop_loss, CloseReason.STOP_LOSS, when)
                changed.append(position)
            elif hit_target:
                self._settle(position, position.take_profit, CloseReason.TAKE_PROFIT, when)
                changed.append(position)
        return changed

    def close(self, ticket: int, price: float, reason: CloseReason = CloseReason.MANUAL,
              when: pd.Timestamp | None = None) -> Position | None:
        for position in self.positions:
            if position.ticket == ticket and position.is_live:
                self._settle(position, price, reason, when)
                return position
        return None

    # -- state -------------------------------------------------------------

    def open_positions(self) -> list[Position]:
        return [p for p in self.positions if p.state is OrderState.OPEN]

    def pending_orders(self) -> list[Position]:
        return [p for p in self.positions if p.state is OrderState.PENDING]

    def closed_positions(self) -> list[Position]:
        return [p for p in self.positions if p.state is OrderState.CLOSED]

    def to_frame(self) -> pd.DataFrame:
        rows = [p.as_dict() for p in self.positions]
        return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["ticket", "state"])

    def summary(self) -> str:
        closed = self.closed_positions()
        r = [p.r_multiple for p in closed if np.isfinite(p.r_multiple)]
        wins = sum(1 for x in r if x > 0)
        return "\n".join([
            f"balance        : {self.balance:,.2f} (from {self.starting_balance:,.2f})",
            f"orders placed  : {len(self.positions)}",
            f"filled / open  : {len(closed)} closed, {len(self.open_positions())} open, "
            f"{len(self.pending_orders())} pending",
            f"win rate       : {wins / len(r):.1%}" if r else "win rate       : n/a",
            f"total R        : {sum(r):.2f}" if r else "total R        : n/a",
        ])

    # -- internals ---------------------------------------------------------

    def _settle(self, position: Position, price: float, reason: CloseReason,
                when: pd.Timestamp | None) -> None:
        slip = self.spread_cost + self.slippage
        exit_price = price - slip if position.bullish else price + slip
        entry = position.fill_price if np.isfinite(position.fill_price) else position.entry
        move = (exit_price - entry) if position.bullish else (entry - exit_price)

        position.exit_price = exit_price
        position.state = OrderState.CLOSED
        position.close_reason = reason
        position.closed_at = when
        position.r_multiple = float(move / position.risk) if position.risk > 0 else float("nan")
        position.profit = float(move * position.volume)
        self.balance += position.profit
        self._journal("CLOSED", position)

    def _journal(self, event: str, position: Position) -> None:
        """Every decision and every fill, appended as JSON lines."""
        if self.journal_path is None:
            return
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        record = {"event": event, "logged_at": datetime.now(timezone.utc).isoformat(),
                  **position.as_dict()}
        with self.journal_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str) + "\n")
