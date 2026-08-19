"""The oracle test: nothing may change when the future is removed.

This is the test the whole project's credibility rests on. For every bar t we
re-run detection over `frame[:t+1]` and require the result to be identical to
the full-history run's `as_of(t)`. If any swing appears earlier, moves, or
vanishes retroactively, this fails -- which is exactly what "no repainting"
and "no look-ahead" mean in code rather than in prose.
"""

from __future__ import annotations

import numpy as np
import pytest

from config.smc_rules import (
    BOSMode,
    BreakConfig,
    LiquidityConfig,
    SMCRules,
    StructureConfig,
    SwingConfig,
    SwingMode,
)
from liquidity.levels import build_liquidity
from structure.breaks import detect_breaks
from structure.market_structure import build_structure
from structure.swings import detect_swings
from tests.conftest import make_frame


def _random_walk_frame(n: int = 260, seed: int = 3):
    rng = np.random.default_rng(seed)
    closes = 2000 + rng.normal(0, 1.2, n).cumsum()
    wick = np.abs(rng.normal(0.5, 0.25, n))
    return make_frame(list(closes + wick), list(closes - wick))


def _fingerprint(swings) -> list[tuple]:
    return [
        (s.kind.value, s.formed_at_index, round(s.price, 6), s.confirmed_at_index)
        for s in swings
    ]


@pytest.mark.parametrize(
    "rules",
    [
        SMCRules(),
        SMCRules(swing=SwingConfig(swing_left=2, swing_right=2, min_swing_atr=0.0)),
        SMCRules(swing=SwingConfig(swing_left=5, swing_right=2, min_swing_atr=1.0)),
        SMCRules(swing=SwingConfig(mode=SwingMode.FIXED_LOOKBACK, min_swing_atr=0.3)),
        SMCRules(swing=SwingConfig(mode=SwingMode.ATR_ADAPTIVE, min_swing_atr=0.5)),
    ],
    ids=["default", "fast", "asymmetric", "fixed_lookback", "atr_adaptive"],
)
def test_swings_never_repaint(rules):
    frame = _random_walk_frame()
    full = detect_swings(frame, rules)

    for t in range(len(frame)):
        truncated = detect_swings(frame.iloc[: t + 1], rules)
        assert _fingerprint(truncated.current) == _fingerprint(full.as_of(t)), (
            f"state at bar {t} differs between a live run and history"
        )


def test_a_swing_is_never_known_before_its_right_window_completes():
    frame = _random_walk_frame(200, seed=9)
    series = detect_swings(frame, SMCRules())

    for swing in series.swings:
        assert swing.confirmed_at_index > swing.formed_at_index
        assert series.as_of(swing.formed_at_index).count(swing) == 0
        assert swing in series.as_of(swing.confirmed_at_index)


def test_supersession_only_ever_happens_at_a_confirmation_bar():
    frame = _random_walk_frame(300, seed=17)
    series = detect_swings(frame, SMCRules())
    confirmations = {s.confirmed_at_index for s in series.swings}

    for swing in series.swings:
        if swing.superseded_at_index is not None:
            assert swing.superseded_at_index in confirmations
            assert swing.superseded_at_index > swing.confirmed_at_index


def test_detection_is_deterministic():
    frame = _random_walk_frame(150, seed=5)
    a = detect_swings(frame, SMCRules())
    b = detect_swings(frame, SMCRules())
    assert _fingerprint(a.swings) == _fingerprint(b.swings)
    assert a.reject_counts() == b.reject_counts()


def _structure_fingerprint(state) -> tuple:
    def level(swing):
        return None if swing is None else (swing.formed_at_index, round(swing.price, 6))

    return (
        state.bias.value,
        level(state.structural_high),
        level(state.structural_low),
        level(state.protected_high),
        level(state.protected_low),
        state.last_high_label.value if state.last_high_label else None,
        state.last_low_label.value if state.last_low_label else None,
    )


@pytest.mark.parametrize(
    "rules",
    [
        SMCRules(structure=StructureConfig(track_internal=False)),
        SMCRules(swing=SwingConfig(swing_left=2, swing_right=2, min_swing_atr=0.0),
                 structure=StructureConfig(range_atr_mult=0.0, track_internal=False)),
        SMCRules(swing=SwingConfig(swing_left=4, swing_right=2, min_swing_atr=1.0),
                 structure=StructureConfig(equal_tolerance_atr=0.2, track_internal=False)),
    ],
    ids=["default", "fine", "coarse_with_equals"],
)
def test_market_structure_never_repaints(rules):
    """Bias, structural levels and protected levels must all be as-of-honest."""
    frame = _random_walk_frame(220, seed=13)
    full = build_structure(frame, rules)

    for t in range(len(frame)):
        truncated = build_structure(frame.iloc[: t + 1], rules)
        assert _structure_fingerprint(truncated.current) == _structure_fingerprint(full.state_at(t)), (
            f"structure at bar {t} differs between a live run and history"
        )
        assert truncated.bias_at(t) is full.bias_at(t)


def test_labels_are_fixed_at_confirmation():
    """A swing's HH/HL/LH/LL label must never be rewritten by later bars."""
    frame = _random_walk_frame(200, seed=29)
    rules = SMCRules(structure=StructureConfig(track_internal=False))
    full = build_structure(frame, rules)

    for t in range(40, len(frame), 7):
        truncated = build_structure(frame.iloc[: t + 1], rules)
        seen = [(l.formed_at_index, l.label.value) for l in truncated.labels_known_at(t)]
        expected = [(l.formed_at_index, l.label.value) for l in full.labels_known_at(t)]
        assert seen == expected


def test_bias_timeline_matches_recomputed_state_everywhere():
    """The fast forward pass and the from-scratch state must never diverge."""
    frame = _random_walk_frame(300, seed=31)
    ms = build_structure(frame, SMCRules(structure=StructureConfig(track_internal=False)))
    assert all(ms.state_at(t).bias is ms.bias_at(t) for t in range(len(frame)))


def test_appending_a_new_bar_never_edits_older_state():
    """Simulates live operation: feed bars one at a time and watch history."""
    frame = _random_walk_frame(180, seed=21)
    previous_states: dict[int, list[tuple]] = {}

    for t in range(30, len(frame)):
        series = detect_swings(frame.iloc[: t + 1], SMCRules())
        for earlier in range(30, t):
            state = _fingerprint(series.as_of(earlier))
            if earlier in previous_states:
                assert previous_states[earlier] == state, (
                    f"the past changed: state at bar {earlier} was rewritten at bar {t}"
                )
            else:
                previous_states[earlier] = state


# --------------------------------------------------------- Phase 4: breaks

def _break_fingerprint(events) -> list[tuple]:
    return [
        (e.type.value, e.direction.value, e.index, round(e.broken_level, 6),
         e.bias_after.value)
        for e in events
    ]


@pytest.mark.parametrize(
    "rules",
    [
        SMCRules(structure=StructureConfig(track_internal=False)),
        SMCRules(swing=SwingConfig(swing_left=2, swing_right=2, min_swing_atr=0.0),
                 structure=StructureConfig(range_atr_mult=0.0, track_internal=False)),
        SMCRules(structure=StructureConfig(track_internal=False),
                 breaks=BreakConfig(bos_mode=BOSMode.WICK_OR_CLOSE, mss_min_displacement=0.4)),
    ],
    ids=["default", "fine", "wick_mode"],
)
def test_breaks_never_repaint(rules):
    """An event's `index` must be the first bar it could have been known."""
    frame = _random_walk_frame(200, seed=23)
    full_structure = build_structure(frame, rules)
    full = detect_breaks(frame, full_structure, rules)

    for t in range(len(frame)):
        window = frame.iloc[: t + 1]
        live = detect_breaks(window, build_structure(window, rules), rules)
        assert _break_fingerprint(live.events) == _break_fingerprint(full.events_known_at(t)), (
            f"break history at bar {t} differs between a live run and history"
        )
        assert live.bias_at(t) is full.bias_at(t)


def test_break_confirmed_bias_is_reproducible_at_every_bar():
    frame = _random_walk_frame(240, seed=37)
    rules = SMCRules(structure=StructureConfig(track_internal=False))
    full = build_structure(frame, rules, with_breaks=True)

    for t in range(0, len(frame), 5):
        window = frame.iloc[: t + 1]
        live = build_structure(window, rules, with_breaks=True)
        assert live.current.bias is full.state_at(t).bias


def test_displacement_of_a_bar_never_changes():
    """Displacement reads one bar plus a causal ATR -- it cannot be revised."""
    from common.indicators import wilder_atr
    from config.smc_rules import DisplacementConfig
    from structure.displacement import displacement_at

    frame = _random_walk_frame(120, seed=41)
    config = DisplacementConfig()
    full_atr = wilder_atr(frame, 14)

    for t in range(20, len(frame), 9):
        window = frame.iloc[: t + 1]
        live = displacement_at(window, t, bullish=True,
                               atr_value=float(wilder_atr(window, 14).iloc[t]), config=config)
        historical = displacement_at(frame, t, bullish=True,
                                     atr_value=float(full_atr.iloc[t]), config=config)
        assert live.score == historical.score


# ------------------------------------------------------ Phase 5: liquidity

def _pool_fingerprint(pools, index) -> list[tuple]:
    return sorted(
        (p.kind.value, p.confirmed_at_index, round(p.price_at(index), 6),
         p.status_at(index).value, p.touch_count_at(index), p.member_count_at(index))
        for p in pools
    )


@pytest.mark.parametrize(
    "rules",
    [
        SMCRules(structure=StructureConfig(track_internal=False)),
        SMCRules(swing=SwingConfig(swing_left=2, swing_right=2, min_swing_atr=0.0),
                 structure=StructureConfig(track_internal=False),
                 liquidity=LiquidityConfig(equal_tolerance_atr=0.3)),
    ],
    ids=["default", "fine_with_wide_equals"],
)
def test_liquidity_pools_never_repaint(rules):
    """A pool's price, status, touches and member count must be as-of honest."""
    frame = _random_walk_frame(180, seed=53)
    full = build_liquidity(frame, rules)

    for t in range(0, len(frame), 3):
        live = build_liquidity(frame.iloc[: t + 1], rules)
        assert _pool_fingerprint(live.known_at(t), t) == _pool_fingerprint(full.known_at(t), t), (
            f"liquidity at bar {t} differs between a live run and history"
        )


def test_a_pool_is_never_known_before_the_bar_that_creates_it():
    frame = _random_walk_frame(200, seed=59)
    liquidity = build_liquidity(frame, SMCRules())

    for pool in liquidity.pools:
        assert pool.confirmed_at_index >= pool.created_at_index
        assert not pool.is_known_at(pool.confirmed_at_index - 1)


def test_pool_status_only_moves_forward():
    """INTACT -> SWEPT -> CONSUMED, never backwards."""
    order = {"INTACT": 0, "SWEPT": 1, "CONSUMED": 2}
    frame = _random_walk_frame(200, seed=61)
    liquidity = build_liquidity(frame, SMCRules())

    for pool in liquidity.pools:
        seen = [order[pool.status_at(t).value]
                for t in range(pool.confirmed_at_index, len(frame))]
        assert seen == sorted(seen)


def test_equal_level_clusters_only_grow_forward():
    frame = _random_walk_frame(200, seed=67)
    liquidity = build_liquidity(frame, SMCRules())

    for pool in liquidity.pools:
        if pool.cluster is None:
            continue
        counts = [pool.member_count_at(t) for t in range(pool.confirmed_at_index, len(frame))]
        assert counts == sorted(counts)
