"""Walk-forward validation: the only result that means anything.

An in-sample backtest tells you what would have worked if you had known the
answers. Walk-forward asks the harder question: using only what was knowable
at the time, would this have worked NEXT?

    TRAIN | VALIDATION | OOS,  chronological, expanding, stepped forward

Two guards make it honest (docs/PROBABILITY_METHODOLOGY.md §7):

  * **Purge gap.** A gap of at least `max_hold_bars` sits between train and
    test, so a training trade cannot still be open when a test trade starts.
  * **As-of probabilities.** Inside the test window each signal is priced from
    outcomes resolved before it -- never from the window it is being tested on.

Thresholds may be tuned on TRAIN/VALIDATION only. Re-tuning after seeing OOS
converts OOS into training data; a run that does that is marked TAINTED, and
this module will not do it for you.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from backtesting.engine import BacktestMode, BacktestResult, run_backtest
from backtesting.metrics import Metrics, compute_metrics
from config.decision_config import DEFAULT_DECISION_CONFIG, DecisionConfig
from config.probability_config import DEFAULT_PROBABILITY_CONFIG, ProbabilityConfig
from config.smc_rules import DEFAULT_RULES, SMCRules
from database.models import SetupStore
from probability.calibration import CalibrationReport, calibration_report


@dataclass
class Fold:
    """One train/validation/test split, by bar index."""

    number: int
    train_start: int
    train_end: int
    validation_end: int
    test_start: int
    test_end: int
    purge_bars: int

    @property
    def spans(self) -> str:
        return (f"train {self.train_start}-{self.train_end} | "
                f"val -{self.validation_end} | purge {self.purge_bars} | "
                f"test {self.test_start}-{self.test_end}")


@dataclass
class FoldResult:
    fold: Fold
    trades: int = 0
    metrics: Metrics = field(default_factory=Metrics)
    calibration: CalibrationReport = field(default_factory=CalibrationReport)
    first_test_bar: pd.Timestamp | None = None
    last_test_bar: pd.Timestamp | None = None


@dataclass
class WalkForwardResult:
    folds: list[FoldResult] = field(default_factory=list)
    combined: Metrics = field(default_factory=Metrics)
    combined_calibration: CalibrationReport = field(default_factory=CalibrationReport)
    tainted: bool = False
    note: str = ""

    def to_frame(self) -> pd.DataFrame:
        rows = [{
            "fold": f.fold.number,
            "test_from": f.first_test_bar,
            "test_to": f.last_test_bar,
            "trades": f.trades,
            "win_rate": f.metrics.win_rate,
            "expectancy_r": f.metrics.expectancy_r,
            "total_r": f.metrics.total_r,
            "profit_factor": f.metrics.profit_factor,
            "max_dd_r": f.metrics.max_drawdown_r,
        } for f in self.folds]
        return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["fold", "trades"])

    def summary(self) -> str:
        lines = ["walk-forward, out-of-sample only", ""]
        lines.append(self.to_frame().to_string(index=False))
        lines += ["", "combined out-of-sample:", self.combined.summary()]
        if self.combined_calibration.n:
            lines += ["", "calibration:", self.combined_calibration.summary()]
        if self.tainted:
            lines += ["", "*** TAINTED: thresholds were re-tuned after seeing OOS ***"]
        return "\n".join(lines)


def make_folds(
    n_bars: int,
    *,
    folds: int = 4,
    train_fraction: float = 0.5,
    validation_fraction: float = 0.15,
    purge_bars: int = 96,
) -> list[Fold]:
    """Expanding-window folds with a purge gap before each test window."""
    if folds < 1 or n_bars < 100:
        return []

    first_train = int(n_bars * train_fraction)
    remaining = n_bars - first_train
    if remaining <= purge_bars + folds:
        return []

    test_size = max(1, (remaining - purge_bars) // folds)
    output: list[Fold] = []
    for i in range(folds):
        train_end = first_train + i * test_size
        validation_end = train_end + int(n_bars * validation_fraction * 0)  # reserved
        test_start = train_end + purge_bars
        test_end = min(test_start + test_size - 1, n_bars - 1)
        if test_start >= n_bars - 1:
            break
        output.append(Fold(number=i + 1, train_start=0, train_end=train_end,
                           validation_end=validation_end or train_end,
                           test_start=test_start, test_end=test_end,
                           purge_bars=purge_bars))
    return output


def run_walk_forward(
    frame: pd.DataFrame,
    store: SetupStore,
    rules: SMCRules = DEFAULT_RULES,
    *,
    folds: int = 4,
    train_fraction: float = 0.5,
    purge_bars: int | None = None,
    probability_config: ProbabilityConfig = DEFAULT_PROBABILITY_CONFIG,
    decision_config: DecisionConfig = DEFAULT_DECISION_CONFIG,
) -> WalkForwardResult:
    """Backtest each test window in DECISION mode, with as-of probabilities."""
    purge = purge_bars if purge_bars is not None else rules.setups.max_hold_bars
    result = WalkForwardResult()
    splits = make_folds(len(frame), folds=folds, train_fraction=train_fraction,
                        purge_bars=purge)
    if not splits:
        result.note = "not enough bars for the requested folds"
        return result

    all_r: list[float] = []
    predicted: list[float] = []
    actual: list[float] = []

    for fold in splits:
        window = frame.iloc[: fold.test_end + 1]
        run: BacktestResult = run_backtest(
            window, rules, mode=BacktestMode.DECISION, store=store,
            probability_config=probability_config, decision_config=decision_config,
        )
        # Keep only trades signalled inside the test window; everything before
        # it is the training period the model was allowed to learn from.
        test_trades = [t for t in run.trades
                       if fold.test_start <= t.candidate.signal_index <= fold.test_end]

        fold_result = FoldResult(fold=fold, trades=len(test_trades))
        if test_trades:
            r = [t.r_multiple for t in test_trades]
            fold_result.metrics = compute_metrics(
                r,
                mae=[t.outcome.mae_r for t in test_trades],
                mfe=[t.outcome.mfe_r for t in test_trades],
                bars_held=[t.outcome.bars_to_result for t in test_trades],
            )
            fold_result.first_test_bar = test_trades[0].candidate.signal_time
            fold_result.last_test_bar = test_trades[-1].candidate.signal_time
            all_r += r
            for trade in test_trades:
                if np.isfinite(trade.probability):
                    predicted.append(trade.probability)
                    actual.append(1.0 if trade.outcome.outcome.hit_tp1 else 0.0)
            fold_result.calibration = calibration_report(
                np.array([t.probability for t in test_trades]),
                np.array([1.0 if t.outcome.outcome.hit_tp1 else 0.0 for t in test_trades]),
            )
        result.folds.append(fold_result)

    result.combined = compute_metrics(all_r)
    if predicted:
        result.combined_calibration = calibration_report(np.array(predicted),
                                                         np.array(actual))
    return result
