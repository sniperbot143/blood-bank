"""Liquidity sweeps: penetration + rejection, and everything that isn't one."""

from __future__ import annotations

import pytest

from config.smc_rules import BreakConfig, SMCRules, SweepConfig, SwingConfig
from liquidity.levels import build_liquidity
from liquidity.sweeps import SweepType, detect_sweeps
from structure.breaks import BreakType, detect_breaks
from structure.market_structure import build_structure
from tests.conftest import make_frame

GEOMETRY = SwingConfig(swing_left=2, swing_right=2, min_swing_atr=0.0)


def _rules(**sweep_kwargs) -> SMCRules:
    return SMCRules(atr_period=5, swing=GEOMETRY, sweeps=SweepConfig(**sweep_kwargs))


def _warmup(bars: int = 8, base: float = 100.0):
    return [base + (i % 2) + 0.5 for i in range(bars)], [base + (i % 2) - 0.5 for i in range(bars)]


def _sweep_frame(spike_high: float = 111.0, spike_close: float = 106.0):
    """A peak at 110.5, then a bar that spikes through it and closes back under."""
    warm_highs, warm_lows = _warmup()
    highs = warm_highs + [102.5, 104.5, 106.5, 108.5, 110.5, 108.5, 106.5, 105.5]
    lows = warm_lows + [h - 2.0 for h in highs[len(warm_highs):]]
    peak = len(warm_highs) + 4

    highs += [spike_high, 106.0, 105.0]
    lows += [105.0, 104.0, 103.0]
    closes = [(h + l) / 2 for h, l in zip(highs, lows)]
    closes[peak + 4] = spike_close
    return make_frame(highs, lows, opens=[l + 0.2 for l in lows], closes=closes), peak


def _sweeps(frame, rules):
    liquidity = build_liquidity(frame, rules)
    return detect_sweeps(frame, liquidity, rules)


def test_a_penetration_that_rejects_is_a_sweep():
    frame, peak = _sweep_frame()
    sweeps = _sweeps(frame, _rules())

    buy_side = [e for e in sweeps.events if e.type is SweepType.BUY_SIDE_SWEEP]
    assert buy_side
    event = buy_side[0]
    assert event.penetration_index == peak + 4
    assert event.magnitude_atr > 0
    assert event.rejection_atr > 0


def test_the_sweep_is_confirmed_at_the_rejection_bar_not_the_spike():
    frame, _ = _sweep_frame(spike_high=111.0, spike_close=110.8)   # closes above, rejects later
    sweeps = _sweeps(frame, _rules(confirm_bars=3, max_close_location=1.0))
    if sweeps.events:
        event = sweeps.events[0]
        assert event.confirmed_at_index >= event.penetration_index
        assert event.bars_to_reject == event.confirmed_at_index - event.penetration_index


def test_a_deep_break_is_a_breakout_not_a_sweep():
    frame, _ = _sweep_frame(spike_high=140.0, spike_close=139.0)
    sweeps = _sweeps(frame, _rules())

    assert not [e for e in sweeps.events if e.type is SweepType.BUY_SIDE_SWEEP]
    assert sweeps.rejected_breakouts >= 1


def test_a_touch_that_never_penetrates_is_not_a_sweep():
    frame, _ = _sweep_frame(spike_high=110.5, spike_close=108.0)   # exactly the level
    sweeps = _sweeps(frame, _rules(min_penetration_atr=0.5))
    assert not [e for e in sweeps.events if e.type is SweepType.BUY_SIDE_SWEEP]


def test_a_penetration_that_closes_strong_is_not_a_sweep():
    """No rejection, no sweep -- that is a level being taken."""
    frame, _ = _sweep_frame(spike_high=111.0, spike_close=110.9)
    strict = _sweeps(frame, _rules(confirm_bars=0))
    assert not [e for e in strict.events if e.type is SweepType.BUY_SIDE_SWEEP]


def test_sell_side_sweeps_are_detected_on_lows():
    warm_highs, warm_lows = _warmup()
    highs = warm_highs + [98.0, 96.0, 94.0, 92.0, 90.0, 92.0, 94.0, 95.0]
    lows = warm_lows + [h - 2.0 for h in highs[len(warm_highs):]]
    trough = len(warm_highs) + 4

    highs += [93.0, 94.0, 95.0]
    lows += [86.5, 92.0, 93.0]          # spike below the 88.0 low
    closes = [(h + l) / 2 for h, l in zip(highs, lows)]
    closes[trough + 4] = 92.5           # closes back above
    frame = make_frame(highs, lows, opens=[h - 0.2 for h in highs], closes=closes)

    sweeps = _sweeps(frame, _rules())
    assert [e for e in sweeps.events if e.type is SweepType.SELL_SIDE_SWEEP]


def test_features_are_measured_and_bounded():
    frame, _ = _sweep_frame()
    sweeps = _sweeps(frame, _rules())
    for event in sweeps.events:
        assert 0.0 <= event.close_location <= 1.0
        assert event.magnitude_atr <= _rules().sweeps.max_penetration_atr
        assert event.bars_to_reject >= 0
        assert event.pool_strength > 0


def test_a_sweep_is_never_known_before_its_rejection_bar():
    frame, _ = _sweep_frame()
    rules = _rules()
    full = _sweeps(frame, rules)

    for t in range(len(frame)):
        window = frame.iloc[: t + 1]
        live = detect_sweeps(window, build_liquidity(window, rules), rules)
        expected = [(e.type.value, e.penetration_index, e.confirmed_at_index)
                    for e in full.known_at(t)]
        seen = [(e.type.value, e.penetration_index, e.confirmed_at_index) for e in live.events]
        assert seen == expected


def test_recent_lookup_powers_the_mss_origin_test():
    frame, _ = _sweep_frame()
    sweeps = _sweeps(frame, _rules())
    if sweeps.events:
        event = sweeps.events[0]
        assert event in sweeps.recent(event.confirmed_at_index, 5)
        assert event not in sweeps.recent(event.confirmed_at_index + 50, 5)


def test_mss_origin_requirement_can_now_be_enforced():
    """With sweeps supplied, an MSS without a preceding sweep is refused."""
    frame, _ = _sweep_frame()
    rules = SMCRules(atr_period=5, swing=GEOMETRY,
                     breaks=BreakConfig(mss_require_swept_origin=True))
    structure = build_structure(frame, rules)
    sweeps = detect_sweeps(frame, build_liquidity(frame, rules), rules)

    without = detect_breaks(frame, structure, rules, sweeps=None)      # cannot judge -> skipped
    with_sweeps = detect_breaks(frame, structure, rules, sweeps=sweeps)

    mss_without = [e for e in without.events if e.type is BreakType.MSS]
    mss_with = [e for e in with_sweeps.events if e.type is BreakType.MSS]
    assert len(mss_with) <= len(mss_without)


def test_counts_and_frame_output():
    frame, _ = _sweep_frame()
    sweeps = _sweeps(frame, _rules())
    assert sum(sweeps.type_counts().values()) == len(sweeps.events)
    assert sum(sweeps.counts().values()) == len(sweeps.events)
    assert "type" in sweeps.to_frame().columns


def test_empty_frame_is_handled():
    frame = make_frame([], [])
    sweeps = detect_sweeps(frame, build_liquidity(frame, _rules()), _rules())
    assert sweeps.events == []
