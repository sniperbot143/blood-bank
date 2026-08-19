"""Fair value gaps and their inversions."""

from __future__ import annotations

import pytest

from config.smc_rules import FVGConfig, SMCRules
from imbalance.fvg import FVGDirection, FVGStatus, detect_fvgs
from imbalance.ifvg import detect_ifvgs
from tests.conftest import make_frame

RULES = SMCRules(atr_period=5, fvg=FVGConfig(require_displacement=False, min_size_atr=0.0))


def _warm(bars: int = 8, base: float = 100.0):
    return ([base + (i % 2) + 0.5 for i in range(bars)],
            [base + (i % 2) - 0.5 for i in range(bars)])


def _bullish_gap_frame(retrace_low: float | None = None, extra: int = 4):
    """Three bars where bar 3's low sits above bar 1's high."""
    highs, lows = _warm()
    highs += [102.0, 106.0, 110.0]        # bar i-2 high = 102.0
    lows += [100.5, 103.0, 104.0]         # bar i   low  = 104.0  -> gap [102, 104]
    for k in range(extra):
        level = retrace_low if (retrace_low is not None and k == 1) else 108.0
        highs.append(110.0)
        lows.append(level)
    closes = [(h + l) / 2 for h, l in zip(highs, lows)]
    return make_frame(highs, lows, opens=[l + 0.1 for l in lows], closes=closes)


def _target(gaps, bottom: float = 102.0):
    """The designed gap. A trending fixture makes several; pick ours by level."""
    return next(g for g in gaps.gaps
                if g.direction is FVGDirection.BULLISH and abs(g.bottom - bottom) < 1e-6)


def test_a_three_bar_bullish_gap_is_detected():
    gaps = detect_fvgs(_bullish_gap_frame(), RULES)
    gap = _target(gaps)
    assert gap.bottom == pytest.approx(102.0)
    assert gap.top == pytest.approx(104.0)
    assert gap.mid == pytest.approx(103.0)


def test_a_gap_is_knowable_on_its_third_bar():
    gaps = detect_fvgs(_bullish_gap_frame(), RULES)
    gap = _target(gaps)

    assert gap.confirmed_at_index == gap.formed_at_index
    assert not gap.is_known_at(gap.confirmed_at_index - 1)
    assert gap.is_known_at(gap.confirmed_at_index)


def test_bearish_gaps_are_the_mirror():
    highs, lows = _warm()
    highs += [102.0, 99.0, 96.0]          # bar i high 96 < bar i-2 low 100.5
    lows += [100.5, 96.0, 94.0]
    closes = [(h + l) / 2 for h, l in zip(highs, lows)]
    frame = make_frame(highs, lows, opens=[h - 0.1 for h in highs], closes=closes)

    gaps = detect_fvgs(frame, RULES)
    bearish = [g for g in gaps.gaps if g.direction is FVGDirection.BEARISH]
    assert bearish
    gap = next(g for g in bearish if abs(g.bottom - 96.0) < 1e-6)
    assert gap.top == pytest.approx(100.5)      # the earlier bar's low
    assert gap.bottom < gap.top


def test_an_untouched_gap_stays_fresh():
    gaps = detect_fvgs(_bullish_gap_frame(), RULES)
    gap = _target(gaps)
    last = gaps.n_bars - 1

    assert gap.status_at(last) is FVGStatus.FRESH
    assert gap.fill_at(last) == 0.0


def test_a_partial_retrace_fills_the_gap_partially():
    gaps = detect_fvgs(_bullish_gap_frame(retrace_low=103.5), RULES)
    gap = _target(gaps)
    last = gaps.n_bars - 1

    assert 0.0 < gap.fill_at(last) < 0.5
    assert gap.status_at(last) is FVGStatus.PARTIAL


def test_trading_past_consequent_encroachment_mitigates_it():
    gaps = detect_fvgs(_bullish_gap_frame(retrace_low=102.5), RULES)
    gap = _target(gaps)
    last = gaps.n_bars - 1

    assert gap.fill_at(last) >= 0.5
    assert gap.status_at(last) is FVGStatus.MITIGATED


def test_a_full_fill_invalidates_it():
    gaps = detect_fvgs(_bullish_gap_frame(retrace_low=101.0), RULES)
    gap = _target(gaps)
    last = gaps.n_bars - 1

    assert gap.fill_at(last) == pytest.approx(1.0)
    assert gap.status_at(last) is FVGStatus.INVALIDATED
    assert gap not in gaps.active_at(last)


def test_fill_never_decreases():
    gaps = detect_fvgs(_bullish_gap_frame(retrace_low=102.5), RULES)
    gap = _target(gaps)
    fills = [gap.fill_at(t) for t in range(gaps.n_bars)]
    assert fills == sorted(fills)


def test_small_gaps_are_rejected_by_the_size_filter():
    frame = _bullish_gap_frame()
    strict = SMCRules(atr_period=5, fvg=FVGConfig(require_displacement=False, min_size_atr=5.0))
    gaps = detect_fvgs(frame, strict)
    assert not gaps.gaps
    assert gaps.rejected_small > 0


def test_displacement_can_be_required_of_the_middle_bar():
    frame = _bullish_gap_frame()
    demanding = SMCRules(atr_period=5,
                         fvg=FVGConfig(require_displacement=True, min_displacement=0.99))
    assert not detect_fvgs(frame, demanding).gaps


def test_gaps_never_repaint():
    frame = _bullish_gap_frame(retrace_low=102.5)
    full = detect_fvgs(frame, RULES)

    for t in range(len(frame)):
        live = detect_fvgs(frame.iloc[: t + 1], RULES)
        seen = [(g.direction.value, g.formed_at_index, round(g.top, 6), round(g.fill_at(t), 6))
                for g in live.known_at(t)]
        expected = [(g.direction.value, g.formed_at_index, round(g.top, 6), round(g.fill_at(t), 6))
                    for g in full.known_at(t)]
        assert seen == expected


def test_leaves_imbalance_feeds_the_displacement_score():
    gaps = detect_fvgs(_bullish_gap_frame(), RULES)
    gap = gaps.gaps[0]
    assert gaps.leaves_imbalance_at(gap.confirmed_at_index) == 1.0
    assert gaps.leaves_imbalance_at(0) == 0.0


def test_nearest_and_counts():
    gaps = detect_fvgs(_bullish_gap_frame(), RULES)
    last = gaps.n_bars - 1
    assert gaps.nearest(103.0, last) is not None
    assert sum(gaps.counts(last).values()) == len(gaps.known_at(last))
    assert "direction" in gaps.to_frame(last).columns


# ---------------------------------------------------------------- inversions

def _inversion_frame():
    """A bullish gap, a close below it, then a rejection back below it."""
    highs, lows = _warm()
    highs += [102.0, 106.0, 110.0]
    lows += [100.5, 103.0, 104.0]              # gap [102, 104]
    # break down through and close below the gap
    highs += [105.0, 103.0]
    lows += [101.0, 99.0]
    # return into the range and close below it again -> inversion
    highs += [103.5, 103.0]
    lows += [99.5, 98.0]
    closes = [(h + l) / 2 for h, l in zip(highs, lows)]
    closes[-4] = 101.5      # the kill close, below 102
    closes[-2] = 101.0      # re-entered the range, closed back below
    closes[-1] = 99.0
    return make_frame(highs, lows, opens=[h - 0.1 for h in highs], closes=closes)


def test_a_killed_and_reclaimed_gap_becomes_an_ifvg():
    frame = _inversion_frame()
    gaps = detect_fvgs(frame, RULES)
    inversions = detect_ifvgs(frame, gaps, RULES)

    assert inversions.inversions
    inverse = inversions.inversions[0]
    assert inverse.direction is FVGDirection.BEARISH        # polarity flipped
    assert inverse.confirmed_at_index > inverse.invalidated_at_index


def test_an_ifvg_is_not_known_before_the_reclaim_bar():
    frame = _inversion_frame()
    inversions = detect_ifvgs(frame, detect_fvgs(frame, RULES), RULES)
    if inversions.inversions:
        inverse = inversions.inversions[0]
        assert not inverse.is_known_at(inverse.confirmed_at_index - 1)
        assert inverse.is_known_at(inverse.confirmed_at_index)


def test_a_gap_that_is_never_closed_through_does_not_invert():
    frame = _bullish_gap_frame()
    assert not detect_ifvgs(frame, detect_fvgs(frame, RULES), RULES).inversions


def test_the_reclaim_window_is_enforced():
    frame = _inversion_frame()
    impatient = SMCRules(atr_period=5,
                         fvg=FVGConfig(require_displacement=False, min_size_atr=0.0,
                                       ifvg_reclaim_bars=1))
    patient = SMCRules(atr_period=5,
                       fvg=FVGConfig(require_displacement=False, min_size_atr=0.0,
                                     ifvg_reclaim_bars=20))
    gaps = detect_fvgs(frame, RULES)
    assert len(detect_ifvgs(frame, gaps, impatient).inversions) <= \
           len(detect_ifvgs(frame, gaps, patient).inversions)


def test_empty_frame_is_handled():
    frame = make_frame([], [])
    gaps = detect_fvgs(frame, RULES)
    assert gaps.gaps == []
    assert detect_ifvgs(frame, gaps, RULES).inversions == []
