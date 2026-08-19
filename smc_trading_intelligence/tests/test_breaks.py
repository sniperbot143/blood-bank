"""BOS / CHOCH / MSS -- three distinct events, tested as three distinct events."""

from __future__ import annotations

import pandas as pd
import pytest

from config.smc_rules import BOSMode, BreakConfig, SMCRules, StructureConfig, SwingConfig
from structure.breaks import BreakType, Direction, breaks_level, detect_breaks
from structure.displacement import DisplacementClass
from structure.market_structure import Bias, BiasSource, build_structure
from tests.conftest import make_frame

RULES = SMCRules(
    swing=SwingConfig(swing_left=2, swing_right=2, min_swing_atr=0.0),
    structure=StructureConfig(range_atr_mult=0.0, track_internal=False),
)


def _flat_bars(path: list[float], half: float = 0.5):
    """Small, bodyless bars: structure without displacement."""
    return ([c + half for c in path], [c - half for c in path], list(path), list(path))


def _uptrend_then_collapse(collapse_low: float = 104.0, collapse_body: bool = True):
    """Warm-up → uptrend with two BOS → one wide bar through the protected low."""
    path = [100, 101, 100, 101, 100, 101, 100, 101, 100, 101,      # 0-9   ATR warm-up
            102, 104, 106, 104, 103,                                # 10-14 leg up, low ~103
            105, 107, 109, 111, 109, 108,                           # 15-20 higher, low ~108
            110, 112, 114, 113]                                     # 21-24 continuation
    highs, lows, opens, closes = _flat_bars(path)

    # bar 25: the break bar. With a body it displaces; without, it merely drifts
    # below the level on a bar that closes mid-range.
    highs.append(114.5)
    lows.append(collapse_low)
    opens.append(114.5 if collapse_body else collapse_low + 3.0)
    closes.append(collapse_low if collapse_body else collapse_low + 2.5)

    # 26-28: drift, never displacement -- so a pending CHOCH gets no second
    # chance to become an MSS here.
    for c in (105, 104, 105):
        highs.append(c + 0.5)
        lows.append(c - 0.5)
        opens.append(c)
        closes.append(c)

    return make_frame(highs, lows, opens=opens, closes=closes)


def _breaks(frame, rules: SMCRules = RULES):
    structure = build_structure(frame, rules)
    return structure, detect_breaks(frame, structure, rules)


# ------------------------------------------------------------- the predicate

@pytest.mark.parametrize(
    "mode,close,high,expected",
    [
        (BOSMode.CLOSE_ONLY, 101.0, 102.0, True),
        (BOSMode.CLOSE_ONLY, 99.0, 102.0, False),     # wick through, close under
        (BOSMode.WICK_OR_CLOSE, 99.0, 102.0, True),
        (BOSMode.WICK_OR_CLOSE, 99.0, 99.5, False),
    ],
)
def test_break_predicate_respects_mode(mode, close, high, expected):
    broken, _ = breaks_level(high, 98.0, close, 100.0, direction=Direction.BULLISH, mode=mode)
    assert broken is expected


def test_a_wick_through_a_level_is_not_a_close_break():
    broken, _ = breaks_level(105.0, 98.0, 99.0, 100.0,
                             direction=Direction.BULLISH, mode=BOSMode.CLOSE_ONLY)
    assert broken is False


# -------------------------------------------------------------------- BOS

def test_bos_fires_on_a_close_through_the_structural_high():
    _, breaks = _breaks(_uptrend_then_collapse())
    bos = [e for e in breaks.events if e.type is BreakType.BOS]

    assert len(bos) == 2
    assert all(e.direction is Direction.BULLISH for e in bos)
    assert bos[0].bias_after is Bias.BULLISH


def test_bos_confirms_bias_it_never_flips_it():
    _, breaks = _breaks(_uptrend_then_collapse())
    for event in breaks.events:
        if event.type is BreakType.BOS:
            assert event.bias_after is event.direction.bias


def test_the_same_level_cannot_produce_two_bos():
    _, breaks = _breaks(_uptrend_then_collapse())
    levels = [e.broken_level_formed_index for e in breaks.events if e.type is BreakType.BOS]
    assert len(levels) == len(set(levels))


def test_displacement_confirmation_mode_suppresses_drifting_breaks():
    frame = _uptrend_then_collapse()
    strict = SMCRules(
        swing=RULES.swing, structure=RULES.structure,
        breaks=BreakConfig(bos_mode=BOSMode.DISPLACEMENT_CONFIRMATION),
    )
    _, loose_breaks = _breaks(frame)
    _, strict_breaks = _breaks(frame, strict)

    loose_bos = sum(1 for e in loose_breaks.events if e.type is BreakType.BOS)
    strict_bos = sum(1 for e in strict_breaks.events if e.type is BreakType.BOS)
    assert strict_bos < loose_bos      # the bodyless uptrend breaks no longer qualify


# ------------------------------------------------------------------ CHOCH

def test_choch_fires_on_the_first_close_through_the_protected_level():
    _, breaks = _breaks(_uptrend_then_collapse(collapse_body=False))
    choch = [e for e in breaks.events if e.type is BreakType.CHOCH]

    assert len(choch) == 1
    assert choch[0].direction is Direction.BEARISH
    assert choch[0].bias_before is Bias.BULLISH
    assert choch[0].bias_after is Bias.RANGE       # a warning, not a reversal


def test_a_choch_without_displacement_does_not_flip_bias():
    frame = _uptrend_then_collapse(collapse_body=False)
    _, breaks = _breaks(frame)

    assert not [e for e in breaks.events if e.type is BreakType.MSS]
    assert breaks.bias_at(len(frame) - 1) is not Bias.BEARISH


def test_the_same_protected_level_cannot_choch_twice():
    _, breaks = _breaks(_uptrend_then_collapse(collapse_body=False))
    levels = [e.broken_level_formed_index for e in breaks.events if e.type is BreakType.CHOCH]
    assert len(levels) == len(set(levels))


# -------------------------------------------------------------------- MSS

def test_mss_fires_when_the_choch_bar_displaces():
    _, breaks = _breaks(_uptrend_then_collapse(collapse_body=True))
    mss = [e for e in breaks.events if e.type is BreakType.MSS]

    assert len(mss) == 1
    assert mss[0].direction is Direction.BEARISH
    assert mss[0].bias_after is Bias.BEARISH               # this one DOES flip bias
    assert mss[0].displacement.grade is DisplacementClass.STRONG


def test_every_mss_is_also_recorded_as_a_choch():
    _, breaks = _breaks(_uptrend_then_collapse(collapse_body=True))
    mss = next(e for e in breaks.events if e.type is BreakType.MSS)
    choch = next(e for e in breaks.events if e.type is BreakType.CHOCH)

    assert mss.choch_index == choch.index
    assert mss.broken_level == choch.broken_level
    # ...and they are stored as separate events, never merged.
    assert mss.type is not choch.type


def test_the_displacement_threshold_decides_choch_versus_mss():
    """Same bars, same CHOCH -- only the threshold decides if it is a reversal."""
    frame = _uptrend_then_collapse(collapse_body=False)   # break bar scores ~0.32

    _, strict = _breaks(frame)                             # default threshold 0.55
    lenient = SMCRules(swing=RULES.swing, structure=RULES.structure,
                       breaks=BreakConfig(mss_min_displacement=0.25))
    _, loose = _breaks(frame, lenient)

    assert not [e for e in strict.events if e.type is BreakType.MSS]
    assert [e for e in strict.events if e.type is BreakType.CHOCH]   # the CHOCH still stands
    assert [e for e in loose.events if e.type is BreakType.MSS]


def test_a_pending_choch_expires_if_displacement_never_arrives():
    frame = _uptrend_then_collapse(collapse_body=False)
    short_window = SMCRules(
        swing=RULES.swing, structure=RULES.structure,
        breaks=BreakConfig(mss_confirm_window=1),
    )
    _, breaks = _breaks(frame, short_window)
    assert breaks.expired_choch >= 1


def test_mss_requires_a_level_with_real_structure_behind_it():
    frame = _uptrend_then_collapse(collapse_body=True)
    demanding = SMCRules(
        swing=RULES.swing, structure=RULES.structure,
        breaks=BreakConfig(mss_min_legs=20),
    )
    _, breaks = _breaks(frame, demanding)
    assert not [e for e in breaks.events if e.type is BreakType.MSS]


# ------------------------------------------------------- precedence & bias

def test_one_bar_never_produces_both_a_reversal_and_a_continuation():
    _, breaks = _breaks(_uptrend_then_collapse(collapse_body=True))
    by_bar: dict[int, set] = {}
    for event in breaks.events:
        by_bar.setdefault(event.index, set()).add(event.type)

    for types in by_bar.values():
        assert not (BreakType.BOS in types and (BreakType.MSS in types or BreakType.CHOCH in types))


def test_bias_timeline_follows_the_events():
    """The bar's bias is set by the LAST event on it (a bar can hold CHOCH+MSS)."""
    frame = _uptrend_then_collapse(collapse_body=True)
    _, breaks = _breaks(frame)

    last_on_bar = {event.index: event for event in breaks.events}
    for index, event in last_on_bar.items():
        assert breaks.bias_at(index) is event.bias_after
    first_on_bar = {}
    for event in breaks.events:
        first_on_bar.setdefault(event.index, event)
    for index, event in first_on_bar.items():
        if index > 0:
            assert breaks.bias_at(index - 1) is event.bias_before


def test_attaching_breaks_switches_the_bias_source():
    frame = _uptrend_then_collapse(collapse_body=True)
    swing_only = build_structure(frame, RULES)
    confirmed = build_structure(frame, RULES, with_breaks=True)

    assert swing_only.current.bias_source is BiasSource.SWING_SEQUENCE
    assert confirmed.current.bias_source is BiasSource.BOS_CONFIRMED
    # The Phase 3 reading stays available for comparison.
    assert confirmed.swing_sequence_bias_at(len(frame) - 1) is swing_only.current.bias


def test_break_confirmed_bias_commits_earlier_than_label_bias():
    """A BOS is evidence now; waiting for the next HL is evidence later.

    On the same bars the break engine is BULLISH from the first BOS, while the
    Phase 3 label rule is still RANGE because the confirming HL has not landed.
    """
    frame = _uptrend_then_collapse(collapse_body=False)
    structure = build_structure(frame, RULES, with_breaks=True)
    first_bos = next(e for e in structure.breaks.events if e.type is BreakType.BOS)

    assert structure.bias_at(first_bos.index) is Bias.BULLISH
    assert structure.swing_sequence_bias_at(first_bos.index) is Bias.RANGE

    committed_earlier = [
        t for t in range(first_bos.index, len(frame))
        if structure.bias_at(t) is Bias.BULLISH
        and structure.swing_sequence_bias_at(t) is Bias.RANGE
    ]
    assert committed_earlier


# ------------------------------------------------------------------ guards

def test_a_gap_bar_cannot_confirm_a_break():
    frame = _uptrend_then_collapse(collapse_body=True)
    gapped = frame.copy()
    gapped.iloc[25, gapped.columns.get_loc("gap_before")] = True

    _, normal = _breaks(frame)
    _, with_gap = _breaks(gapped)

    assert [e for e in normal.events if e.index == 25]
    assert not [e for e in with_gap.events if e.index == 25]


def test_the_gap_guard_can_be_switched_off():
    frame = _uptrend_then_collapse(collapse_body=True)
    gapped = frame.copy()
    gapped.iloc[25, gapped.columns.get_loc("gap_before")] = True

    permissive = SMCRules(swing=RULES.swing, structure=RULES.structure,
                          breaks=BreakConfig(reject_on_gap_bar=False))
    _, breaks = _breaks(gapped, permissive)
    assert [e for e in breaks.events if e.index == 25]


def test_empty_frame_produces_no_events():
    frame = make_frame([], [])
    structure = build_structure(frame, RULES)
    breaks = detect_breaks(frame, structure, RULES)

    assert breaks.events == []
    assert breaks.bias_at(0) is Bias.RANGE


def test_series_helpers():
    frame = _uptrend_then_collapse(collapse_body=True)
    _, breaks = _breaks(frame)

    assert sum(breaks.counts().values()) == len(breaks.events)
    assert len(breaks.events_known_at(20)) < len(breaks.events)
    assert breaks.last(BreakType.MSS).type is BreakType.MSS
    assert breaks.last(BreakType.MSS, index=10) is None
    assert isinstance(breaks.to_frame(), pd.DataFrame)
