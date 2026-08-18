"""Swing engine tests. Every fixture has a known-correct answer by construction."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from config.smc_rules import SMCRules, SwingConfig, SwingMode
from structure.swings import RejectReason, SwingKind, detect_swings
from tests.conftest import make_frame, zigzag

# min_swing_atr=0 isolates the geometry from the size filter.
GEOMETRY = SMCRules(swing=SwingConfig(swing_left=3, swing_right=3, min_swing_atr=0.0))


def _triangle():
    """Up to 110, down to 90, up to 105 -- one clear high, one clear low."""
    highs, lows = zigzag([110, 90, 105], bars_per_leg=6)
    return make_frame(highs, lows)


def test_detects_the_obvious_high_and_low():
    series = detect_swings(_triangle(), GEOMETRY)

    kinds = [(s.kind, s.formed_at_index) for s in series.swings]
    assert kinds == [(SwingKind.HIGH, 5), (SwingKind.LOW, 11)]
    assert series.swings[0].price == pytest.approx(110.4)
    assert series.swings[1].price == pytest.approx(89.6)


def test_confirmation_lags_formation_by_swing_right():
    series = detect_swings(_triangle(), GEOMETRY)
    for swing in series.swings:
        assert swing.confirmed_at_index == swing.formed_at_index + GEOMETRY.swing.swing_right
        assert swing.confirmed_at == _triangle().index[swing.confirmed_at_index]


def test_a_swing_is_invisible_before_its_confirmation_bar():
    series = detect_swings(_triangle(), GEOMETRY)
    high = series.swings[0]

    assert series.as_of(high.confirmed_at_index - 1) == []
    assert high.status_at(high.formed_at_index) == "DEVELOPING"
    assert series.as_of(high.confirmed_at_index) == [high]
    assert high.status_at(high.confirmed_at_index) == "CONFIRMED"


def test_swing_right_of_one_confirms_faster():
    rules = SMCRules(swing=SwingConfig(swing_left=1, swing_right=1, min_swing_atr=0.0))
    series = detect_swings(_triangle(), rules)
    assert all(s.confirmed_at_index == s.formed_at_index + 1 for s in series.swings)


def test_plateau_resolves_to_its_first_bar():
    #                          v---- equal highs at index 4 and 5
    highs = [10, 11, 12, 13, 14, 14, 13, 12, 11, 10]
    lows = [h - 1 for h in highs]
    series = detect_swings(make_frame(highs, lows),
                           SMCRules(swing=SwingConfig(swing_left=2, swing_right=2, min_swing_atr=0.0)))

    highs_found = [s.formed_at_index for s in series.swings if s.kind is SwingKind.HIGH]
    assert highs_found == [4]


def test_fixed_lookback_mode_allows_ties_on_both_sides():
    highs = [10, 11, 12, 13, 14, 14, 13, 12, 11, 10]
    lows = [h - 1 for h in highs]
    rules = SMCRules(swing=SwingConfig(mode=SwingMode.FIXED_LOOKBACK, swing_left=2,
                                       swing_right=2, min_swing_atr=0.0))
    found = [s.formed_at_index for s in detect_swings(make_frame(highs, lows), rules).swings
             if s.kind is SwingKind.HIGH]
    # Both plateau bars are window maxima; the collapse rule keeps the first
    # (equal price never displaces an existing swing).
    assert found == [4]


def test_atr_adaptive_mode_produces_alternating_swings():
    highs, lows = zigzag([110, 90, 115, 85, 120], bars_per_leg=6)
    rules = SMCRules(swing=SwingConfig(mode=SwingMode.ATR_ADAPTIVE, swing_left=3,
                                       swing_right=3, min_swing_atr=0.0))
    series = detect_swings(make_frame(highs, lows), rules)

    assert series.alternates()
    assert len(series.swings) >= 3
    # Adaptive windows must still be honest about when they were knowable.
    assert all(s.confirmed_at_index == s.formed_at_index + s.right for s in series.swings)


# ---------------------------------------------------------------- ATR filter

def _noisy_then_big():
    """Warm-up oscillation, a big rally, a 1.3-point pullback, a higher high."""
    highs, lows = zigzag([104, 100, 104, 100, 104, 100, 104, 100, 120, 119.5, 126, 108],
                         bars_per_leg=4)
    return make_frame(highs, lows)


def test_small_wiggles_are_rejected_by_the_atr_filter():
    frame = _noisy_then_big()
    loose = detect_swings(frame, SMCRules(swing=SwingConfig(swing_left=3, swing_right=3,
                                                            min_swing_atr=0.0)))
    strict = detect_swings(frame, SMCRules(swing=SwingConfig(swing_left=3, swing_right=3,
                                                             min_swing_atr=3.0)))

    assert len(strict.swings) < len(loose.swings)
    assert strict.reject_counts()["ATR_FILTER"] == 5
    # The 1.3-point pullback at index 39 is noise at this threshold.
    rejected_at = {r.formed_at_index for r in strict.rejected if r.reason is RejectReason.ATR_FILTER}
    assert 39 in rejected_at


def test_swings_before_atr_is_seeded_are_rejected_not_guessed():
    series = detect_swings(_noisy_then_big(),
                           SMCRules(swing=SwingConfig(swing_left=3, swing_right=3, min_swing_atr=3.0)))
    assert series.reject_counts()["NO_ATR"] == 3
    assert all(s.formed_at_index >= 14 for s in series.swings)


def test_zero_threshold_disables_the_filter_entirely():
    series = detect_swings(_noisy_then_big(),
                           SMCRules(swing=SwingConfig(swing_left=3, swing_right=3, min_swing_atr=0.0)))
    assert series.rejected == []


def test_strength_is_measured_in_atr():
    series = detect_swings(_noisy_then_big(),
                           SMCRules(swing=SwingConfig(swing_left=3, swing_right=3, min_swing_atr=0.0)))
    rally_high = next(s for s in series.swings if s.formed_at_index == 35)
    assert rally_high.strength_atr > 5           # a big leg
    assert np.isnan(series.swings[0].strength_atr)  # before ATR is seeded


# ------------------------------------------------------------- supersession

def test_a_higher_high_supersedes_the_previous_one_without_erasing_it():
    series = detect_swings(_noisy_then_big(),
                           SMCRules(swing=SwingConfig(swing_left=3, swing_right=3, min_swing_atr=3.0)))

    first_high = next(s for s in series.swings if s.formed_at_index == 35)
    higher = next(s for s in series.swings if s.formed_at_index == 43)

    assert first_high.superseded_at_index == higher.confirmed_at_index == 46
    assert first_high.superseded_by_index == 43

    # Before bar 46 the old high was legitimately the current one...
    assert [s.formed_at_index for s in series.as_of(45)] == [31, 35]
    # ...and only from bar 46 does the new one take over. Nothing repainted.
    assert [s.formed_at_index for s in series.as_of(46)] == [31, 43]
    assert first_high.status_at(46) == "SUPERSEDED"


def test_a_lower_high_does_not_displace_the_current_one():
    highs, lows = zigzag([104, 100, 104, 100, 104, 100, 104, 100, 126, 124, 125, 108],
                         bars_per_leg=4)
    series = detect_swings(make_frame(highs, lows),
                           SMCRules(swing=SwingConfig(swing_left=3, swing_right=3, min_swing_atr=3.0)))

    reasons = {r.reason for r in series.rejected}
    assert RejectReason.NOT_EXTREME in reasons
    live_highs = [s.price for s in series.highs()]
    assert max(live_highs) == pytest.approx(126.4)


def test_the_live_chain_always_alternates():
    rng = np.random.default_rng(11)
    closes = 100 + rng.normal(0, 1, 400).cumsum()
    highs = list(closes + 0.6)
    lows = list(closes - 0.6)
    series = detect_swings(make_frame(highs, lows), SMCRules())

    assert series.alternates()
    for cut in range(0, 400, 37):
        chain = series.as_of(cut)
        assert all(a.kind is not b.kind for a, b in zip(chain, chain[1:]))


# ------------------------------------------------------------------ guards

def test_a_forming_bar_is_refused():
    frame = _triangle().copy()
    frame.iloc[-1, frame.columns.get_loc("is_closed")] = False
    with pytest.raises(ValueError, match="unclosed bar"):
        detect_swings(frame, GEOMETRY)


def test_empty_and_short_frames_are_handled():
    assert detect_swings(make_frame([], []), GEOMETRY).swings == []
    short = detect_swings(make_frame([10, 11, 10], [9, 10, 9]), GEOMETRY)
    assert short.swings == []       # window is wider than the data
    assert short.n_bars == 3


def test_swings_spanning_a_data_gap_are_flagged():
    highs, lows = zigzag([110, 90], bars_per_leg=6)
    frame = make_frame(highs, lows)
    frame.iloc[6, frame.columns.get_loc("gap_before")] = True   # inside the peak's window

    series = detect_swings(frame, GEOMETRY)
    peak = next(s for s in series.swings if s.formed_at_index == 5)
    assert peak.spans_gap is True


def test_gap_spanning_swings_can_be_rejected_by_config():
    highs, lows = zigzag([110, 90], bars_per_leg=6)
    frame = make_frame(highs, lows)
    frame.iloc[6, frame.columns.get_loc("gap_before")] = True

    rules = SMCRules(swing=SwingConfig(swing_left=3, swing_right=3, min_swing_atr=0.0,
                                       reject_across_gaps=True))
    series = detect_swings(frame, rules)
    assert 5 not in [s.formed_at_index for s in series.swings]
    assert series.reject_counts()["SPANS_GAP"] == 1


def test_symbol_and_timeframe_are_carried_through():
    series = detect_swings(_triangle(), GEOMETRY)
    assert series.symbol == "TESTm" and series.timeframe == "M5"
    assert all(s.symbol == "TESTm" for s in series.swings)


def test_to_frame_exposes_the_live_chain():
    series = detect_swings(_triangle(), GEOMETRY)
    out = series.to_frame()
    assert len(out) == 2
    assert list(out["kind"]) == ["HIGH", "LOW"]
    assert isinstance(out.index, pd.DatetimeIndex)


def test_last_returns_the_most_recent_of_a_kind():
    series = detect_swings(_triangle(), GEOMETRY)
    assert series.last(SwingKind.HIGH).formed_at_index == 5
    assert series.last(SwingKind.LOW).formed_at_index == 11
    assert series.last().formed_at_index == 11
    assert series.last(SwingKind.LOW, index=10) is None    # not yet confirmed


def test_rules_hash_changes_with_parameters():
    assert SMCRules().rules_hash != SMCRules(swing=SwingConfig(swing_left=5)).rules_hash
    assert SMCRules().rules_hash == SMCRules().rules_hash
