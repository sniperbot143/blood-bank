"""Market structure: labels, protected levels, and the bias timeline."""

from __future__ import annotations

import pytest

from config.smc_rules import SMCRules, StructureConfig, SwingConfig
from structure.market_structure import (
    Bias,
    BiasSource,
    Scope,
    StructureLabel,
    analyze_structure,
    build_structure,
)
from structure.swings import SwingKind, detect_swings
from tests.conftest import make_frame, zigzag

# Geometry-only rules: swings from shape alone, no size filter, no range veto.
RULES = SMCRules(
    swing=SwingConfig(swing_left=2, swing_right=2, min_swing_atr=0.0),
    structure=StructureConfig(range_atr_mult=0.0, track_internal=False),
)


def _structure(legs: list[float], rules: SMCRules = RULES, bars_per_leg: int = 5):
    highs, lows = zigzag(legs, bars_per_leg=bars_per_leg)
    frame = make_frame(highs, lows)
    return frame, build_structure(frame, rules)


# ------------------------------------------------------------------- labels

def test_uptrend_is_labelled_hh_and_hl():
    _, ms = _structure([110, 104, 118, 112, 126, 120, 134])
    labels = [l.label for l in ms.labels]

    assert labels[0] is StructureLabel.FIRST_HIGH
    assert labels[1] is StructureLabel.FIRST_LOW
    assert set(labels[2:]) == {StructureLabel.HH, StructureLabel.HL}


def test_downtrend_is_labelled_lh_and_ll():
    _, ms = _structure([96, 104, 90, 100, 84, 94])
    labels = [l.label for l in ms.labels][2:]
    assert set(labels) == {StructureLabel.LH, StructureLabel.LL}


def test_equal_levels_are_labelled_eqh_and_eql():
    loose = SMCRules(
        swing=SwingConfig(swing_left=2, swing_right=2, min_swing_atr=0.0),
        structure=StructureConfig(equal_tolerance_atr=1.0, range_atr_mult=0.0,
                                  track_internal=False),
    )
    _, ms = _structure([110, 100, 110.05, 100.05, 110.02], loose)
    labels = {l.label for l in ms.labels}
    assert StructureLabel.EQH in labels
    assert StructureLabel.EQL in labels


def test_tolerance_of_zero_never_produces_equal_labels():
    strict = SMCRules(
        swing=SwingConfig(swing_left=2, swing_right=2, min_swing_atr=0.0),
        structure=StructureConfig(equal_tolerance_atr=0.0, range_atr_mult=0.0,
                                  track_internal=False),
    )
    _, ms = _structure([110, 100, 110.05, 100.05, 110.02], strict)
    assert not {StructureLabel.EQH, StructureLabel.EQL} & {l.label for l in ms.labels}


def test_a_swing_is_labelled_against_the_one_it_supersedes():
    """A higher high is an HH even though it removes the old high from the chain."""
    highs, lows = zigzag([104, 100, 104, 100, 104, 100, 104, 100, 120, 119.5, 126, 108],
                         bars_per_leg=4)
    frame = make_frame(highs, lows)
    rules = SMCRules(
        swing=SwingConfig(swing_left=3, swing_right=3, min_swing_atr=3.0),
        structure=StructureConfig(range_atr_mult=0.0, track_internal=False),
    )
    ms = build_structure(frame, rules)

    superseding = next(l for l in ms.labels if l.formed_at_index == 43)
    assert superseding.label is StructureLabel.HH
    assert superseding.compared_to_index == 35     # the swing it replaced


# --------------------------------------------------------------------- bias

def test_bias_turns_bullish_only_once_both_labels_agree():
    frame, ms = _structure([110, 104, 118, 112, 126, 120, 134])

    first_hh = next(l for l in ms.labels if l.label is StructureLabel.HH)
    first_hl = next(l for l in ms.labels if l.label is StructureLabel.HL)

    # An HH alone is not a trend: the low must confirm too.
    assert ms.bias_at(first_hh.confirmed_at_index) is Bias.RANGE
    assert ms.bias_at(first_hl.confirmed_at_index) is Bias.BULLISH
    assert ms.current.bias is Bias.BULLISH


def test_bias_turns_bearish_only_when_both_labels_are_bearish():
    _, ms = _structure([96, 104, 90, 100, 84, 94])
    first_lh = next(l for l in ms.labels if l.label is StructureLabel.LH)
    first_ll = next(l for l in ms.labels if l.label is StructureLabel.LL)

    both_known = max(first_lh.confirmed_at_index, first_ll.confirmed_at_index)
    assert ms.bias_at(both_known - 1) is Bias.RANGE   # one leg still unlabelled
    assert ms.bias_at(both_known) is Bias.BEARISH


def test_a_broken_sequence_is_range_not_a_guess():
    """HH with a still-lower low is genuinely ambiguous -- say so."""
    _, ms = _structure([104, 96, 100, 90, 94, 84, 100, 92, 112])
    transitions = [(c.previous, c.current) for c in ms.changes]

    assert (Bias.RANGE, Bias.BEARISH) in transitions
    assert (Bias.BEARISH, Bias.RANGE) in transitions      # HH lands, low still LL
    assert (Bias.RANGE, Bias.BULLISH) in transitions      # HL confirms the turn
    assert ms.current.bias is Bias.BULLISH


def test_bias_is_range_before_any_structure_exists():
    frame, ms = _structure([110, 104, 118, 112])
    assert ms.bias_at(0) is Bias.RANGE
    assert ms.state_at(0).bias_source is BiasSource.NO_STRUCTURE


def test_a_narrow_dealing_range_vetoes_a_trend_label():
    legs = [110, 104, 118, 112, 126, 120, 134]
    wide = SMCRules(swing=SwingConfig(swing_left=2, swing_right=2, min_swing_atr=0.0),
                    structure=StructureConfig(range_atr_mult=0.0, track_internal=False))
    vetoed = SMCRules(swing=SwingConfig(swing_left=2, swing_right=2, min_swing_atr=0.0),
                      structure=StructureConfig(range_atr_mult=50.0, track_internal=False))

    assert _structure(legs, wide)[1].current.bias is Bias.BULLISH
    assert _structure(legs, vetoed)[1].current.bias is Bias.RANGE


def test_the_forward_pass_and_state_at_always_agree():
    frame, ms = _structure([104, 96, 100, 90, 94, 84, 100, 92, 112])
    assert all(ms.state_at(t).bias is ms.bias_at(t) for t in range(len(frame)))


def test_bias_share_sums_to_one():
    _, ms = _structure([104, 96, 100, 90, 94, 84, 100, 92, 112])
    assert sum(ms.bias_share().values()) == pytest.approx(1.0)


# ---------------------------------------------------------------- the levels

def test_structural_levels_are_the_latest_confirmed_swings():
    frame, ms = _structure([110, 104, 118, 112, 126, 120, 134])
    state = ms.current
    swings = ms.swings.as_of(len(frame) - 1)

    assert state.structural_high is [s for s in swings if s.kind is SwingKind.HIGH][-1]
    assert state.structural_low is [s for s in swings if s.kind is SwingKind.LOW][-1]


def test_protected_low_is_the_low_that_created_the_current_high():
    _, ms = _structure([110, 104, 118, 112, 126, 120, 134])
    state = ms.current

    assert state.protected_low is not None
    assert state.protected_low.formed_at_index < state.structural_high.formed_at_index
    # ...and it is the LAST low before that high, not an older one.
    lows_before = [s for s in ms.swings.current
                   if s.kind is SwingKind.LOW
                   and s.formed_at_index < state.structural_high.formed_at_index]
    assert state.protected_low is lows_before[-1]


def test_protected_high_mirrors_it_for_lows():
    _, ms = _structure([96, 104, 90, 100, 84, 94])
    state = ms.current
    assert state.protected_high.formed_at_index < state.structural_low.formed_at_index


def test_levels_are_none_before_swings_confirm():
    _, ms = _structure([110, 104])
    state = ms.state_at(0)
    assert state.structural_high is None and state.protected_low is None


def test_dealing_range_width_is_reported_in_atr():
    _, ms = _structure([110, 104, 118, 112, 126, 120, 134])
    state = ms.current
    assert state.range_width == pytest.approx(
        abs(state.structural_high.price - state.structural_low.price)
    )
    assert state.range_width_atr > 0


# -------------------------------------------------------------- internal/API

def test_internal_structure_is_finer_than_external():
    highs, lows = zigzag([110, 104, 118, 112, 126, 120, 134], bars_per_leg=5)
    frame = make_frame(highs, lows)
    rules = SMCRules(
        swing=SwingConfig(swing_left=3, swing_right=3, min_swing_atr=0.0),
        structure=StructureConfig(range_atr_mult=0.0, track_internal=True,
                                  internal_left=1, internal_right=1),
    )
    ms = build_structure(frame, rules)

    assert ms.internal is not None
    assert ms.internal.scope is Scope.INTERNAL
    assert len(ms.internal.labels) >= len(ms.labels)


def test_internal_tracking_can_be_switched_off():
    _, ms = _structure([110, 104, 118, 112])
    assert ms.internal is None


def test_analyze_structure_accepts_a_prebuilt_swing_series():
    highs, lows = zigzag([110, 104, 118, 112, 126], bars_per_leg=5)
    frame = make_frame(highs, lows)
    swings = detect_swings(frame, RULES)
    ms = analyze_structure(frame, swings, RULES)

    assert len(ms.labels) == len(swings.swings)
    assert ms.symbol == "TESTm" and ms.timeframe == "M5"


def test_to_frame_and_counts():
    _, ms = _structure([110, 104, 118, 112, 126, 120, 134])
    out = ms.to_frame()

    assert list(out.columns[:3]) == ["kind", "label", "price"]
    assert sum(ms.label_counts().values()) == len(ms.labels)


def test_describe_is_readable():
    _, ms = _structure([110, 104, 118, 112, 126, 120, 134])
    text = ms.current.describe()
    assert "bias" in text and "protected low" in text


def test_empty_frame_is_handled():
    frame = make_frame([], [])
    ms = build_structure(frame, RULES)
    assert ms.labels == [] and ms.n_bars == 0
    assert ms.bias_at(0) is Bias.RANGE
