"""Charting, backtesting, walk-forward validation and Monte Carlo."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtesting.engine import BacktestMode, assert_no_lookahead, run_backtest
from backtesting.labeling import label_all
from backtesting.metrics import compute_metrics, drawdown, equity_curve
from backtesting.monte_carlo import Resample, compare_methods, run_monte_carlo
from backtesting.walk_forward import make_folds, run_walk_forward
from config.probability_config import ProbabilityConfig
from config.smc_rules import SMCRules, SetupConfig, StructureConfig, SwingConfig
from database.models import SetupStore
from features.context import MarketContext
from signals.setups import detect_setups
from tests.conftest import make_market_frame

RULES = SMCRules(
    swing=SwingConfig(swing_left=2, swing_right=2, min_swing_atr=0.5),
    structure=StructureConfig(track_internal=False),
    setups=SetupConfig(max_hold_bars=40),
)
PROB = ProbabilityConfig(min_samples=10, bootstrap_min_samples=50, bootstrap_iterations=200)


@pytest.fixture(scope="module")
def prepared(tmp_path_factory):
    frame = make_market_frame(900, 11)
    context = MarketContext.build(frame, RULES)
    setups = detect_setups(context, RULES)
    outcomes = label_all(frame, setups.candidates, RULES.setups)

    store = SetupStore(tmp_path_factory.mktemp("bt") / "smc.db")
    run_id = store.start_run(symbol=context.symbol, timeframe=context.timeframe,
                             rules_hash=RULES.rules_hash, bars=context.n_bars)
    store.save_many(run_id, setups.candidates, outcomes, RULES.rules_hash)
    yield frame, context, setups, store
    store.close()


# ----------------------------------------------------------------- metrics

def test_metrics_report_the_headline_numbers():
    r = [1.5, -1.0, 2.0, -1.0, -1.0, 3.0]
    metrics = compute_metrics(r)

    assert metrics.trades == 6
    assert metrics.wins == 3 and metrics.losses == 3
    assert metrics.win_rate == pytest.approx(0.5)
    assert metrics.expectancy_r == pytest.approx(np.mean(r))
    assert metrics.profit_factor == pytest.approx(6.5 / 3.0)
    assert metrics.max_consecutive_losses == 2


def test_a_high_win_rate_with_terrible_payoff_shows_negative_expectancy():
    """The disaster win rate alone would hide."""
    r = [0.1] * 9 + [-1.0]
    metrics = compute_metrics(r)
    assert metrics.win_rate == pytest.approx(0.9)
    assert metrics.expectancy_r < 0


def test_drawdown_measures_peak_to_trough():
    worst, fraction = drawdown(np.array([100.0, 120.0, 90.0, 130.0]))
    assert worst == pytest.approx(30.0)
    assert fraction == pytest.approx(0.25)


def test_equity_curve_compounds():
    curve = equity_curve([1.0, 1.0], risk_per_trade=0.10, starting_balance=1000.0)
    assert curve.iloc[-1] > 1200.0        # compounding beats 2 x 100


def test_empty_input_is_handled():
    assert compute_metrics([]).trades == 0


# --------------------------------------------------------------- backtest

def test_deterministic_backtest_takes_every_filled_candidate(prepared):
    frame, context, setups, store = prepared
    result = run_backtest(frame, RULES, context=context, setups=setups)

    assert result.trades
    assert result.metrics.trades == len(result.trades)
    assert result.considered == len(setups.candidates)
    assert "SUPERSEDED" in result.skipped


def test_a_backtest_never_enters_before_its_signal(prepared):
    frame, context, setups, store = prepared
    result = run_backtest(frame, RULES, context=context, setups=setups)
    assert_no_lookahead(result)              # raises if violated

    for trade in result.trades:
        assert trade.outcome.fill_index > trade.candidate.signal_index


def test_decision_mode_uses_as_of_probabilities(prepared):
    frame, context, setups, store = prepared
    result = run_backtest(frame, RULES, mode=BacktestMode.DECISION, context=context,
                          setups=setups, store=store, probability_config=PROB)

    assert result.mode is BacktestMode.DECISION
    # Decision mode is strictly more selective than taking everything.
    everything = run_backtest(frame, RULES, context=context, setups=setups)
    assert len(result.trades) <= len(everything.trades)
    for trade in result.trades:
        assert trade.decision.is_trade
        assert trade.reason_codes


def test_decision_mode_requires_a_store(prepared):
    frame, context, setups, store = prepared
    with pytest.raises(ValueError, match="needs a SetupStore"):
        run_backtest(frame, RULES, mode=BacktestMode.DECISION, context=context,
                     setups=setups, store=None)


def test_the_trade_list_is_exportable(prepared):
    frame, context, setups, store = prepared
    result = run_backtest(frame, RULES, context=context, setups=setups)
    table = result.to_frame()

    assert len(table) == len(result.trades)
    assert {"signal_time", "r_multiple", "outcome", "decision"} <= set(table.columns)
    assert result.summary()


# ----------------------------------------------------------- walk forward

def test_folds_are_chronological_with_a_purge_gap():
    folds = make_folds(2000, folds=4, train_fraction=0.5, purge_bars=96)

    assert folds
    for fold in folds:
        assert fold.test_start - fold.train_end >= fold.purge_bars
        assert fold.test_start < fold.test_end
    for earlier, later in zip(folds, folds[1:]):
        assert later.train_end >= earlier.train_end      # expanding window
        assert later.test_start > earlier.test_start


def test_too_little_data_produces_no_folds():
    assert make_folds(50) == []


def test_walk_forward_reports_out_of_sample_only(prepared):
    frame, context, setups, store = prepared
    result = run_walk_forward(frame, store, RULES, folds=2, train_fraction=0.5,
                              purge_bars=40, probability_config=PROB)

    assert result.folds
    for fold_result in result.folds:
        assert fold_result.trades >= 0
    assert result.summary()
    assert not result.tainted


def test_walk_forward_trades_fall_inside_their_test_window(prepared):
    frame, context, setups, store = prepared
    result = run_walk_forward(frame, store, RULES, folds=2, train_fraction=0.5,
                              purge_bars=40, probability_config=PROB)

    for fold_result in result.folds:
        if fold_result.first_test_bar is None:
            continue
        fold = fold_result.fold
        assert fold_result.first_test_bar >= frame.index[fold.test_start]
        assert fold_result.last_test_bar <= frame.index[fold.test_end]


# ---------------------------------------------------------- monte carlo

def test_monte_carlo_produces_a_distribution_not_a_prediction():
    r = [1.5, -1.0, 2.0, -1.0, -1.0, 3.0, -1.0, 1.0] * 12
    result = run_monte_carlo(r, iterations=300, seed=1)

    assert len(result.final_r) == 300
    assert result.percentiles(result.final_r)["p5"] < result.percentiles(result.final_r)["p95"]
    assert (result.max_drawdown_r >= 0).all()
    assert result.summary()


def test_a_losing_edge_shows_a_high_risk_of_ruin():
    losing = [-1.0] * 40 + [1.0] * 10
    winning = [2.0] * 30 + [-1.0] * 20
    ruin_losing = run_monte_carlo(losing, iterations=200, risk_per_trade=0.10, seed=2)
    ruin_winning = run_monte_carlo(winning, iterations=200, risk_per_trade=0.10, seed=2)
    assert ruin_losing.risk_of_ruin > ruin_winning.risk_of_ruin


def test_block_resampling_preserves_clustering():
    """Clustered losses should look worse under block resampling than IID."""
    clustered = [-1.0] * 12 + [1.5] * 12 + [-1.0] * 12 + [1.5] * 12
    both = compare_methods(clustered, iterations=300, block_length=8, seed=3)

    iid = np.percentile(both[Resample.IID.value].max_drawdown_r, 95)
    block = np.percentile(both[Resample.BLOCK.value].max_drawdown_r, 95)
    assert block >= iid


def test_monte_carlo_needs_data():
    assert run_monte_carlo([1.0]).iterations == 2000
    assert not len(run_monte_carlo([1.0]).final_r)


# --------------------------------------------------------------- charting

def test_a_chart_is_written_as_a_standalone_file(prepared, tmp_path):
    frame, context, setups, store = prepared
    from visualization.chart import ChartOptions, render_chart

    path = render_chart(context, tmp_path / "chart.html",
                        options=ChartOptions(bars=200))
    assert path.exists()
    html = path.read_text(encoding="utf-8")
    assert "plotly" in html.lower()
    assert len(html) > 10_000


def test_a_chart_only_shows_what_was_knowable(prepared, tmp_path):
    frame, context, setups, store = prepared
    from visualization.chart import ChartOptions, render_chart

    at = 400
    path = render_chart(context, tmp_path / "asof.html", as_of=at,
                        options=ChartOptions(bars=100))
    html = path.read_text(encoding="utf-8")
    # A timestamp after the as-of bar must not appear in the plotted data.
    future = context.frame.index[at + 50]
    assert future.strftime("%Y-%m-%dT%H:%M:%S") not in html
