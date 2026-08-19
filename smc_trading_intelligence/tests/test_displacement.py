"""Displacement scoring: does the bar show real thrust, or is it drift?"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from config.smc_rules import DisplacementConfig
from structure.displacement import (
    DisplacementClass,
    classify,
    close_location,
    displacement_at,
)

CONFIG = DisplacementConfig()


def _bar_frame(open_: float, high: float, low: float, close: float) -> pd.DataFrame:
    return pd.DataFrame(
        {"open": [open_], "high": [high], "low": [low], "close": [close]},
        index=pd.DatetimeIndex(["2024-01-02 09:00"], tz="UTC"),
    )


# ------------------------------------------------------------ close location

@pytest.mark.parametrize(
    "close,bullish,expected",
    [(110.0, True, 1.0), (100.0, True, 0.0), (105.0, True, 0.5),
     (100.0, False, 1.0), (110.0, False, 0.0)],
)
def test_close_location_measures_where_the_bar_closed(close, bullish, expected):
    assert close_location(105.0, 110.0, 100.0, close, bullish=bullish) == pytest.approx(expected)


def test_zero_range_bar_has_no_thrust():
    assert close_location(100.0, 100.0, 100.0, 100.0, bullish=True) == 0.0


# ------------------------------------------------------------------ scoring

def test_a_wide_bodied_bar_closing_on_its_high_is_strong():
    frame = _bar_frame(100.0, 102.1, 99.9, 102.0)
    result = displacement_at(frame, 0, bullish=True, atr_value=1.0, config=CONFIG)

    assert result.grade is DisplacementClass.STRONG
    assert result.score > 0.75
    assert result.body_atr == pytest.approx(2.0)
    assert result.close_location > 0.9


def test_a_doji_is_not_displacement():
    frame = _bar_frame(100.0, 100.6, 99.4, 100.0)
    result = displacement_at(frame, 0, bullish=True, atr_value=1.0, config=CONFIG)

    assert result.grade is DisplacementClass.NONE
    assert not result.is_displacement


def test_direction_matters():
    """A strong down bar scores nothing as bullish displacement."""
    frame = _bar_frame(102.0, 102.1, 99.9, 100.0)

    bearish = displacement_at(frame, 0, bullish=False, atr_value=1.0, config=CONFIG)
    bullish = displacement_at(frame, 0, bullish=True, atr_value=1.0, config=CONFIG)

    assert bearish.grade is DisplacementClass.STRONG
    assert bullish.score < bearish.score
    assert bullish.body_atr < 0


def test_the_same_bar_scores_lower_in_higher_volatility():
    """2 points of range is displacement when ATR is 1, and noise when ATR is 5.

    The close-location component is scale-free on purpose -- it measures
    conviction within the bar -- so the score falls without collapsing to zero.
    What matters is that it drops below the MSS confirmation threshold.
    """
    frame = _bar_frame(100.0, 102.1, 99.9, 102.0)
    calm = displacement_at(frame, 0, bullish=True, atr_value=1.0, config=CONFIG)
    wild = displacement_at(frame, 0, bullish=True, atr_value=5.0, config=CONFIG)

    assert calm.grade is DisplacementClass.STRONG
    assert wild.score < calm.score
    assert wild.score < CONFIG.moderate_threshold


def test_unseeded_atr_returns_unknown_not_a_guess():
    frame = _bar_frame(100.0, 102.0, 99.0, 101.9)
    result = displacement_at(frame, 0, bullish=True, atr_value=float("nan"), config=CONFIG)

    assert np.isnan(result.score)
    assert result.grade is DisplacementClass.NONE


def test_score_is_bounded_to_one():
    frame = _bar_frame(100.0, 130.0, 99.9, 130.0)
    result = displacement_at(frame, 0, bullish=True, atr_value=1.0, config=CONFIG)
    assert result.score <= 1.0


def test_out_of_range_index_raises():
    with pytest.raises(IndexError):
        displacement_at(_bar_frame(1, 2, 0.5, 1.5), 3, bullish=True, atr_value=1.0, config=CONFIG)


# ---------------------------------------------------------------- config

@pytest.mark.parametrize(
    "score,expected",
    [(0.0, DisplacementClass.NONE), (0.34, DisplacementClass.NONE),
     (0.35, DisplacementClass.WEAK), (0.60, DisplacementClass.MODERATE),
     (0.80, DisplacementClass.STRONG), (1.0, DisplacementClass.STRONG)],
)
def test_classification_thresholds(score, expected):
    assert classify(score, CONFIG) is expected


def test_weights_must_sum_to_one():
    with pytest.raises(ValueError, match="must sum to 1.0"):
        DisplacementConfig(body_weight=0.9, range_weight=0.9, close_weight=0.0)


def test_thresholds_must_be_ordered():
    with pytest.raises(ValueError, match="weak <= moderate <= strong"):
        DisplacementConfig(weak_threshold=0.9, moderate_threshold=0.5)


def test_the_imbalance_component_counts_from_phase_8():
    """Leaving an FVG behind is worth 20% of the score (SMC_DEFINITIONS §6)."""
    frame = _bar_frame(100.0, 101.5, 99.9, 101.4)
    without = displacement_at(frame, 0, bullish=True, atr_value=1.0, config=CONFIG)
    with_imb = displacement_at(frame, 0, bullish=True, atr_value=1.0, config=CONFIG,
                               imbalance=1.0)

    assert with_imb.score > without.score
    assert with_imb.score - without.score == pytest.approx(CONFIG.imbalance_weight)

    # ...and the pre-Phase-8 weighting is still expressible.
    phase4 = DisplacementConfig(body_weight=0.50, range_weight=0.25,
                                close_weight=0.25, imbalance_weight=0.0)
    assert displacement_at(frame, 0, bullish=True, atr_value=1.0, config=phase4,
                           imbalance=1.0).score == displacement_at(
        frame, 0, bullish=True, atr_value=1.0, config=phase4).score


def test_a_run_of_bars_can_displace_where_no_single_bar_does():
    """Phase 7: three 0.5-ATR pushes in a row are a leg, not three nothings."""
    from structure.displacement import displacement_run_at

    frame = pd.DataFrame(
        {"open": [100.0, 100.6, 101.2], "high": [100.7, 101.3, 101.9],
         "low": [99.9, 100.5, 101.1], "close": [100.6, 101.2, 101.8]},
        index=pd.date_range("2024-01-02 09:00", periods=3, freq="5min", tz="UTC"),
    )
    single = displacement_at(frame, 2, bullish=True, atr_value=1.0, config=CONFIG)
    run = displacement_run_at(frame, 2, bullish=True, atr_value=1.0, config=CONFIG)

    assert run.score > single.score
    assert run.bars == 3
    assert run.end_index == 2 and run.start_index == 0


def test_a_run_never_looks_past_the_bar_it_ends_on():
    from structure.displacement import displacement_run_at

    frame = pd.DataFrame(
        {"open": [100.0, 100.6, 101.2, 90.0], "high": [100.7, 101.3, 101.9, 102.0],
         "low": [99.9, 100.5, 101.1, 89.0], "close": [100.6, 101.2, 101.8, 89.5]},
        index=pd.date_range("2024-01-02 09:00", periods=4, freq="5min", tz="UTC"),
    )
    full = displacement_run_at(frame, 2, bullish=True, atr_value=1.0, config=CONFIG)
    truncated = displacement_run_at(frame.iloc[:3], 2, bullish=True, atr_value=1.0,
                                    config=CONFIG)
    assert full.score == truncated.score


def test_a_run_requires_a_consistent_direction():
    from structure.displacement import displacement_run_at

    frame = pd.DataFrame(
        {"open": [101.0, 100.6, 101.2], "high": [101.2, 101.3, 101.9],
         "low": [99.9, 100.5, 101.1], "close": [100.0, 101.2, 101.8]},  # bar 0 is down
        index=pd.date_range("2024-01-02 09:00", periods=3, freq="5min", tz="UTC"),
    )
    run = displacement_run_at(frame, 2, bullish=True, atr_value=1.0, config=CONFIG)
    assert run.bars <= 2
