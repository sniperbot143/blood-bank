"""Probability estimation, calibration, confluence scoring and decisions."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtesting.labeling import label_all
from config.decision_config import DecisionConfig, ScoreWeights
from config.probability_config import Prior, ProbabilityConfig, Reliability
from config.smc_rules import SMCRules, SetupConfig, StructureConfig, SwingConfig
from database.models import SetupStore
from features.context import MarketContext
from probability.calibration import IsotonicCalibrator, brier_score, calibration_report, log_loss
from probability.historical_stats import SimilarityKey, find_comparables
from probability.probability import (
    beta_interval,
    block_bootstrap_interval,
    estimate_from_rows,
    estimate_probabilities,
    insufficient,
    wilson_interval,
)
from signals.confluence import score_setup
from signals.decision_engine import Decision, decide
from signals.setups import detect_setups
from tests.conftest import make_market_frame

RULES = SMCRules(
    swing=SwingConfig(swing_left=2, swing_right=2, min_swing_atr=0.5),
    structure=StructureConfig(track_internal=False),
    setups=SetupConfig(max_hold_bars=40),
)
PROB = ProbabilityConfig(min_samples=10, bootstrap_min_samples=40, bootstrap_iterations=200)


@pytest.fixture(scope="module")
def populated(tmp_path_factory):
    """A context, its candidates, and a store containing them."""
    frame = make_market_frame(900, 11)
    context = MarketContext.build(frame, RULES)
    setups = detect_setups(context, RULES)
    outcomes = label_all(frame, setups.candidates, RULES.setups)

    path = tmp_path_factory.mktemp("db") / "smc.db"
    store = SetupStore(path)
    run_id = store.start_run(symbol=context.symbol, timeframe=context.timeframe,
                             rules_hash=RULES.rules_hash, bars=context.n_bars)
    store.save_many(run_id, setups.candidates, outcomes, RULES.rules_hash)
    yield context, setups, store
    store.close()


# ------------------------------------------------------------------ intervals

def test_wilson_interval_brackets_the_estimate():
    low, high = wilson_interval(7, 10)
    assert 0.0 <= low < 0.7 < high <= 1.0


def test_wilson_handles_the_degenerate_cases():
    assert wilson_interval(0, 0) == (0.0, 1.0)
    low, high = wilson_interval(0, 20)
    assert low == 0.0 and high < 0.25


def test_a_bigger_sample_narrows_the_interval():
    small = wilson_interval(7, 10)
    large = wilson_interval(700, 1000)
    assert (large[1] - large[0]) < (small[1] - small[0])


def test_jeffreys_shrinks_less_than_laplace_at_small_n():
    jeffreys = beta_interval(9, 10, Prior.JEFFREYS)
    laplace = beta_interval(9, 10, Prior.LAPLACE)
    assert jeffreys[1] >= laplace[1]


def test_the_block_bootstrap_returns_a_sane_interval():
    successes = np.array([1.0] * 60 + [0.0] * 40)
    low, high = block_bootstrap_interval(successes, block_length=5, config=PROB)
    assert 0.0 <= low < 0.6 < high <= 1.0


# ---------------------------------------------------------------- estimation

def _rows(n: int, wins: int, start: str = "2024-01-01") -> pd.DataFrame:
    outcomes = ["TP1_FIRST"] * wins + ["SL_FIRST"] * (n - wins)
    return pd.DataFrame({
        "outcome": outcomes,
        "r_multiple": [2.0] * wins + [-1.0] * (n - wins),
        "mae_r": [0.3] * n, "mfe_r": [1.2] * n, "bars_to_result": [10] * n,
        "resolved_at": pd.date_range(start, periods=n, freq="D", tz="UTC"),
    })


def test_a_probability_always_carries_its_sample_size():
    rows = _rows(100, 66)
    estimate = estimate_from_rows(rows, "tp1", rows["outcome"] == "TP1_FIRST",
                                  tier="T2", config=PROB)

    assert estimate.sample_size == 100
    assert 0.6 < estimate.probability < 0.72
    assert estimate.confidence_interval_95[0] < estimate.probability
    assert estimate.similarity_tier == "T2"
    assert "sample_size" in estimate.as_dict()


def test_seven_examples_are_marked_very_low_not_treated_as_evidence():
    rows = _rows(7, 5)
    estimate = estimate_from_rows(rows, "tp1", rows["outcome"] == "TP1_FIRST", config=PROB)

    assert estimate.reliability is Reliability.VERY_LOW
    assert not estimate.is_usable


def test_the_same_rate_from_more_data_earns_a_better_reliability():
    small = _rows(40, 26)
    large = _rows(600, 396)
    small_estimate = estimate_from_rows(small, "tp1", small["outcome"] == "TP1_FIRST",
                                        tier="T2", config=PROB)
    large_estimate = estimate_from_rows(large, "tp1", large["outcome"] == "TP1_FIRST",
                                        tier="T2", config=PROB)

    assert large_estimate.reliability.rank > small_estimate.reliability.rank
    assert large_estimate.ci_width < small_estimate.ci_width


def test_recency_weighting_favours_recent_outcomes():
    old_wins = _rows(50, 45, start="2018-01-01")
    recent_losses = _rows(50, 5, start="2024-01-01")
    rows = pd.concat([old_wins, recent_losses], ignore_index=True)
    as_of = pd.Timestamp("2024-06-01", tz="UTC")

    weighted = estimate_from_rows(rows, "tp1", rows["outcome"] == "TP1_FIRST",
                                  as_of=as_of, config=PROB)
    unweighted = estimate_from_rows(
        rows, "tp1", rows["outcome"] == "TP1_FIRST", as_of=as_of,
        config=ProbabilityConfig(min_samples=10, recency_weighting=False),
    )
    assert weighted.probability < unweighted.probability


def test_no_data_is_reported_as_insufficient_not_as_fifty_fifty():
    estimate = insufficient("tp1")
    assert np.isnan(estimate.probability)
    assert estimate.source == "INSUFFICIENT_DATA"
    assert estimate.reliability is Reliability.VERY_LOW


def test_expectancy_and_median_r_are_reported():
    rows = _rows(100, 60)
    estimate = estimate_from_rows(rows, "tp1", rows["outcome"] == "TP1_FIRST", config=PROB)
    assert estimate.expectancy_r > 0
    assert np.isfinite(estimate.median_r)


# ------------------------------------------------------------- similarity

def test_tier_backoff_reports_which_tier_was_used(populated):
    context, setups, store = populated
    candidate = setups.tradeable()[0]
    key = SimilarityKey.from_candidate(candidate)
    found = find_comparables(store, key, config=PROB)

    assert found.tier in ("T1", "T2", "T3", "T4", "T5")
    assert set(found.tier_counts) == {"T1", "T2", "T3", "T4", "T5"}
    assert found.tier_counts["T5"] >= found.tier_counts["T1"]


def test_the_as_of_filter_shrinks_the_comparable_set(populated):
    context, setups, store = populated
    key = SimilarityKey.from_candidate(setups.tradeable()[0])

    everything = find_comparables(store, key, config=PROB)
    if everything.rows.empty:
        pytest.skip("no comparables")
    cutoff = everything.rows["resolved_at"].iloc[len(everything.rows) // 2]
    earlier = find_comparables(store, key, as_of=cutoff, config=PROB)
    assert earlier.size <= everything.size


def test_estimates_cover_tp1_tp2_and_positive_r(populated):
    context, setups, store = populated
    key = SimilarityKey.from_candidate(setups.tradeable()[0])
    estimates = estimate_probabilities(store, key, config=PROB)

    assert set(estimates) == {"tp1", "tp2", "positive_r"}
    for estimate in estimates.values():
        assert estimate.sample_size >= 0


# ------------------------------------------------------------- calibration

def test_a_perfect_forecaster_scores_better_than_the_base_rate():
    actual = np.array([1, 1, 1, 0, 0, 0] * 20)
    perfect = actual.astype(float) * 0.98 + 0.01
    report = calibration_report(perfect, actual)

    assert report.beats_base_rate
    assert report.brier < report.brier_base_rate
    assert report.ece < 0.1


def test_a_useless_forecaster_does_not_beat_the_base_rate():
    rng = np.random.default_rng(3)
    actual = rng.integers(0, 2, 300)
    noise = rng.random(300)
    assert not calibration_report(noise, actual).beats_base_rate


def test_brier_and_log_loss_punish_confident_mistakes():
    actual = np.array([1.0, 1.0, 0.0, 0.0])
    confident_wrong = np.array([0.01, 0.01, 0.99, 0.99])
    hedged = np.array([0.5, 0.5, 0.5, 0.5])

    assert brier_score(confident_wrong, actual) > brier_score(hedged, actual)
    assert log_loss(confident_wrong, actual) > log_loss(hedged, actual)


def test_isotonic_calibration_is_monotone_and_bounded():
    rng = np.random.default_rng(7)
    predicted = rng.random(400)
    actual = (rng.random(400) < predicted * 0.6 + 0.2).astype(float)

    calibrator = IsotonicCalibrator().fit(predicted, actual)
    grid = np.linspace(0, 1, 25)
    mapped = np.asarray(calibrator.transform(grid))

    assert calibrator.fitted
    assert (mapped >= 0).all() and (mapped <= 1).all()
    assert (np.diff(mapped) >= -1e-9).all()


def test_an_unfitted_calibrator_is_a_no_op():
    assert IsotonicCalibrator().transform(0.42) == 0.42


# --------------------------------------------------------------- confluence

def test_the_score_is_bounded_and_itemised(populated):
    context, setups, store = populated
    for candidate in setups.tradeable()[:20]:
        score = score_setup(candidate)
        assert 0.0 <= score.total <= 100.0
        assert sum(c.available for c in score.components) == pytest.approx(100.0)
        assert score.describe()


def test_more_confluence_scores_higher(populated):
    context, setups, store = populated
    scores = [(score_setup(c).total, c.features.values["confluence_count"])
              for c in setups.tradeable()]
    if len({c for _, c in scores}) < 2:
        pytest.skip("fixture lacks variety")
    low = np.mean([s for s, c in scores if c <= 2])
    high = np.mean([s for s, c in scores if c >= 4])
    assert high > low


def test_weights_must_sum_to_one_hundred():
    with pytest.raises(ValueError, match="sum to 100"):
        ScoreWeights(htf_bias=50.0)


def test_the_score_is_not_a_probability(populated):
    """They are different numbers from different code paths, on purpose."""
    context, setups, store = populated
    candidate = setups.tradeable()[0]
    score = score_setup(candidate)
    estimates = estimate_probabilities(store, SimilarityKey.from_candidate(candidate),
                                       config=PROB)
    p = estimates["tp1"].probability
    if np.isfinite(p):
        assert not (abs(score.total / 100.0 - p) < 1e-12 and score.total not in (0.0, 100.0))


# ----------------------------------------------------------------- decision

def _decision_for(candidate, probability, **overrides):
    config = DecisionConfig(**overrides) if overrides else DecisionConfig()
    return decide(candidate, probability, config=config)


def test_insufficient_data_forces_no_trade(populated):
    context, setups, store = populated
    signal = _decision_for(setups.tradeable()[0], insufficient("tp1"))

    assert signal.decision is Decision.NO_TRADE
    assert "INSUFFICIENT_SAMPLE" in signal.reason_codes


def test_low_reliability_forces_no_trade(populated):
    context, setups, store = populated
    rows = _rows(12, 9)
    weak = estimate_from_rows(rows, "tp1", rows["outcome"] == "TP1_FIRST", config=PROB)
    signal = _decision_for(setups.tradeable()[0], weak)

    assert signal.decision is Decision.NO_TRADE
    assert any(r.startswith("LOW_RELIABILITY") for r in signal.reason_codes)


def test_negative_expectancy_is_vetoed_however_pretty_the_setup(populated):
    context, setups, store = populated
    rows = _rows(400, 100)          # 25% win rate
    poor = estimate_from_rows(rows, "tp1", rows["outcome"] == "TP1_FIRST",
                              tier="T2", config=PROB)
    signal = _decision_for(setups.tradeable()[0], poor)

    assert signal.decision is Decision.NO_TRADE
    assert "NEGATIVE_EXPECTANCY" in signal.reason_codes or \
           "BELOW_THRESHOLDS" in signal.reason_codes


def test_a_strong_setup_produces_a_decisive_call(populated):
    context, setups, store = populated
    rows = _rows(600, 480)          # 80%, plenty of data
    strong = estimate_from_rows(rows, "tp1", rows["outcome"] == "TP1_FIRST",
                                tier="T2", config=PROB)

    candidate = max(setups.tradeable(), key=lambda c: score_setup(c).total)
    lenient = DecisionConfig(strong_score=10.0, normal_score=5.0, weak_score=0.0,
                             strong_rr=1.0, normal_rr=1.0, weak_rr=1.0, min_rr=1.0)
    signal = decide(candidate, strong, config=lenient)

    assert signal.decision.is_trade
    assert signal.decision.is_strong
    assert signal.decision.value.endswith("BUY" if candidate.bullish else "SELL")


def test_every_decision_is_auditable(populated):
    context, setups, store = populated
    rows = _rows(300, 200)
    estimate = estimate_from_rows(rows, "tp1", rows["outcome"] == "TP1_FIRST",
                                  tier="T2", config=PROB)
    signal = _decision_for(setups.tradeable()[0], estimate)

    payload = signal.as_dict()
    assert signal.reason_codes
    assert payload["decision"] == signal.decision.value
    assert "sample_size" in payload and "setup_score" in payload
    assert "probability_reliability" in payload
    assert payload["invalidation"]
    assert signal.describe()


def test_thresholds_must_be_ordered():
    with pytest.raises(ValueError, match="must increase"):
        DecisionConfig(strong_probability=0.5, normal_probability=0.8)
