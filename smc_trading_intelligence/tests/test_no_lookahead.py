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

from config.smc_rules import SMCRules, SwingConfig, SwingMode
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
