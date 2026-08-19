"""The backtester: replay history and record what the engine would have done.

Two modes, and the difference matters:

    DETERMINISTIC   take every candidate the setup builder produced
    DECISION        take only what the decision engine approved, using
                    probabilities estimated from data available BEFORE each
                    signal (the as-of query)

The second is the honest one. It is also slower, because each signal needs its
own historical query -- that is the price of not peeking.

Guards, all asserted in tests:
  * a trade is entered no earlier than the bar after its signal
  * probabilities come from `resolved_at < signal_time`
  * overlapping setups are excluded from estimates and, optionally, from trading
  * costs are already inside the R multiples from the labeller
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import pandas as pd

from backtesting.labeling import LabelledOutcome, Outcome, label_outcome
from backtesting.metrics import Metrics, account_drawdown, compute_metrics, equity_curve
from config.decision_config import DEFAULT_DECISION_CONFIG, DecisionConfig
from config.probability_config import DEFAULT_PROBABILITY_CONFIG, ProbabilityConfig
from config.smc_rules import DEFAULT_RULES, SMCRules
from database.models import SetupStore
from features.context import MarketContext
from probability.historical_stats import SimilarityKey
from probability.probability import estimate_probabilities, insufficient
from signals.confluence import score_setup
from signals.decision_engine import Decision, Signal, decide
from signals.setups import SetupCandidate, SetupSeries, detect_setups


class BacktestMode(str, Enum):
    DETERMINISTIC = "DETERMINISTIC"     # every candidate
    DECISION = "DECISION"               # only what the decision engine approved


@dataclass
class BacktestTrade:
    """One executed trade, with the reasoning that produced it."""

    candidate: SetupCandidate
    outcome: LabelledOutcome
    decision: Decision = Decision.NO_TRADE
    probability: float = float("nan")
    reliability: str = ""
    sample_size: int = 0
    score: float = float("nan")
    reason_codes: list[str] = field(default_factory=list)

    @property
    def r_multiple(self) -> float:
        return self.outcome.r_multiple

    def as_dict(self) -> dict:
        return {
            "signal_time": self.candidate.signal_time,
            "signal_index": self.candidate.signal_index,
            "setup_type": self.candidate.setup_type,
            "direction": self.candidate.direction,
            "decision": self.decision.value,
            "probability": self.probability,
            "reliability": self.reliability,
            "sample_size": self.sample_size,
            "score": self.score,
            "entry": self.candidate.levels.entry,
            "stop_loss": self.candidate.levels.stop_loss,
            "rr": self.candidate.levels.rr1,
            "outcome": self.outcome.outcome.value,
            "r_multiple": self.outcome.r_multiple,
            "mae_r": self.outcome.mae_r,
            "mfe_r": self.outcome.mfe_r,
            "bars_to_result": self.outcome.bars_to_result,
            "ambiguous": self.outcome.ambiguous,
        }


@dataclass
class BacktestResult:
    trades: list[BacktestTrade] = field(default_factory=list)
    metrics: Metrics = field(default_factory=Metrics)
    equity: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    mode: BacktestMode = BacktestMode.DETERMINISTIC
    considered: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    symbol: str = ""
    timeframe: str = ""
    first_bar: pd.Timestamp | None = None
    last_bar: pd.Timestamp | None = None

    def to_frame(self) -> pd.DataFrame:
        rows = [t.as_dict() for t in self.trades]
        return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["signal_time", "r_multiple"])

    def summary(self) -> str:
        header = [
            f"mode              : {self.mode.value}",
            f"symbol / timeframe: {self.symbol} {self.timeframe}",
            f"period            : "
            + (f"{self.first_bar:%Y-%m-%d} -> {self.last_bar:%Y-%m-%d}"
               if self.first_bar is not None else "n/a"),
            f"candidates seen   : {self.considered:,}",
            f"skipped           : {self.skipped}",
            "",
        ]
        return "\n".join(header) + self.metrics.summary()


def run_backtest(
    frame: pd.DataFrame,
    rules: SMCRules = DEFAULT_RULES,
    *,
    mode: BacktestMode = BacktestMode.DETERMINISTIC,
    context: MarketContext | None = None,
    setups: SetupSeries | None = None,
    store: SetupStore | None = None,
    probability_config: ProbabilityConfig = DEFAULT_PROBABILITY_CONFIG,
    decision_config: DecisionConfig = DEFAULT_DECISION_CONFIG,
    include_superseded: bool = False,
    risk_per_trade: float = 0.01,
    starting_balance: float = 10_000.0,
) -> BacktestResult:
    """Replay the history and collect the trades the engine would have taken."""
    context = context or MarketContext.build(frame, rules)
    setups = setups or detect_setups(context, rules)

    result = BacktestResult(mode=mode, symbol=context.symbol, timeframe=context.timeframe,
                            first_bar=frame.index[0] if len(frame) else None,
                            last_bar=frame.index[-1] if len(frame) else None)
    skipped: dict[str, int] = {}

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    for candidate in setups.candidates:
        result.considered += 1
        if candidate.superseded and not include_superseded:
            skip("SUPERSEDED")
            continue

        outcome = label_outcome(frame, candidate, rules.setups)
        if outcome.outcome is Outcome.NO_FILL:
            skip("NO_FILL")
            continue
        if outcome.outcome is Outcome.OPEN:
            skip("STILL_OPEN")
            continue

        trade = BacktestTrade(candidate=candidate, outcome=outcome,
                              score=score_setup(candidate, decision_config).total)

        if mode is BacktestMode.DECISION:
            if store is None:
                raise ValueError("DECISION mode needs a SetupStore for as-of probabilities")
            estimates = estimate_probabilities(
                store, SimilarityKey.from_candidate(candidate),
                as_of=candidate.signal_time,          # nothing resolved after this
                config=probability_config,
            )
            estimate = estimates["tp1"]
            signal: Signal = decide(candidate, estimate, config=decision_config,
                                    rules_hash=rules.rules_hash)
            trade.decision = signal.decision
            trade.probability = estimate.probability
            trade.reliability = estimate.reliability.value
            trade.sample_size = estimate.sample_size
            trade.reason_codes = signal.reason_codes
            if not signal.decision.is_trade:
                skip(signal.reason_codes[0] if signal.reason_codes else "NO_TRADE")
                continue
        else:
            trade.decision = (Decision.BUY if candidate.bullish else Decision.SELL)

        result.trades.append(trade)

    result.skipped = skipped
    result.metrics = compute_metrics(
        [t.r_multiple for t in result.trades],
        mae=[t.outcome.mae_r for t in result.trades],
        mfe=[t.outcome.mfe_r for t in result.trades],
        bars_held=[t.outcome.bars_to_result for t in result.trades],
        ambiguous=[t.outcome.ambiguous for t in result.trades],
    )
    result.equity = equity_curve([t.r_multiple for t in result.trades],
                                 risk_per_trade=risk_per_trade,
                                 starting_balance=starting_balance)
    if len(result.equity) > 1:
        _, result.metrics.max_drawdown_pct = account_drawdown(result.equity)
    return result


def assert_no_lookahead(result: BacktestResult) -> None:
    """Cheap structural audit of a finished backtest.

    Catches the two mistakes that would invalidate everything: entering on or
    before the signal bar, and resolving before entering.
    """
    for trade in result.trades:
        signal_index = trade.candidate.signal_index
        if trade.outcome.fill_index is not None:
            assert trade.outcome.fill_index > signal_index, (
                f"trade filled at bar {trade.outcome.fill_index} but was only signalled "
                f"at {signal_index}"
            )
        if trade.outcome.resolved_index is not None and trade.outcome.fill_index is not None:
            assert trade.outcome.resolved_index >= trade.outcome.fill_index, (
                "a trade cannot resolve before it is entered"
            )
