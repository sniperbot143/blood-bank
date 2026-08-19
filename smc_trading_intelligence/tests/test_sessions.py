"""Session windows: assignment, instance boundaries, and DST."""

from __future__ import annotations

import pandas as pd
import pytest

from config.smc_rules import SessionConfig, SessionWindow, SMCRules
from liquidity.sessions import NO_SESSION, build_sessions
from tests.conftest import make_frame


def _hourly_frame(start: str, hours: int, first: float = 100.0) -> pd.DataFrame:
    highs = [first + i * 0.1 + 0.5 for i in range(hours)]
    lows = [first + i * 0.1 - 0.5 for i in range(hours)]
    return make_frame(highs, lows, start=start, minutes=60)


DEFAULTS = SMCRules()


def test_bars_are_assigned_to_the_right_session():
    frame = _hourly_frame("2024-01-02 00:00", 24)
    sessions = build_sessions(frame, DEFAULTS)

    assert sessions.session_at(0) == "ASIAN"        # 00:00 UTC
    assert sessions.session_at(8) == "LONDON"       # 08:00 UTC
    assert sessions.session_at(13) == "NY_AM"       # 13:00 UTC
    assert sessions.session_at(16) == "NY_PM"       # 16:00 UTC
    assert sessions.session_at(22) == NO_SESSION    # 22:00 UTC: outside all windows


def test_one_instance_per_session_per_day():
    frame = _hourly_frame("2024-01-02 00:00", 72)   # three days
    sessions = build_sessions(frame, DEFAULTS)
    assert sessions.counts() == {"ASIAN": 3, "LONDON": 3, "NY_AM": 3, "NY_PM": 3}


def test_instance_records_its_own_high_and_low():
    frame = _hourly_frame("2024-01-02 00:00", 24)
    sessions = build_sessions(frame, DEFAULTS)
    london = next(s for s in sessions.instances if s.name == "LONDON")

    window = frame.iloc[london.start_index:london.end_index + 1]
    assert london.high == pytest.approx(window["high"].max())
    assert london.low == pytest.approx(window["low"].min())
    assert london.bars == len(window)


def test_a_session_is_not_complete_until_its_last_bar_is_past():
    frame = _hourly_frame("2024-01-02 00:00", 24)
    sessions = build_sessions(frame, DEFAULTS)
    london = next(s for s in sessions.instances if s.name == "LONDON")

    assert not london.is_complete_at(london.end_index)      # still forming
    assert london.is_complete_at(london.end_index + 1)
    assert sessions.last_completed("LONDON", london.end_index) is None
    assert sessions.last_completed("LONDON", london.end_index + 1) is london


def test_current_session_lookup():
    frame = _hourly_frame("2024-01-02 00:00", 24)
    sessions = build_sessions(frame, DEFAULTS)
    assert sessions.current(9).name == "LONDON"
    assert sessions.current(22) is None


def test_a_window_can_wrap_past_midnight():
    rules = SMCRules(sessions=SessionConfig(windows=[
        SessionWindow(name="OVERNIGHT", tz="UTC", start="22:00", end="04:00")
    ]))
    frame = _hourly_frame("2024-01-02 20:00", 12)   # 20:00 -> 07:00 next day
    sessions = build_sessions(frame, rules)

    assert sessions.session_at(0) == NO_SESSION     # 20:00
    assert sessions.session_at(2) == "OVERNIGHT"    # 22:00
    assert sessions.session_at(6) == "OVERNIGHT"    # 02:00 next day
    assert sessions.session_at(9) == NO_SESSION     # 05:00
    # The overnight block is ONE session, not two split at midnight.
    assert len(sessions.instances) == 1


def test_named_timezone_windows_follow_dst():
    """A London-local window shifts one hour in UTC when BST starts."""
    rules = SMCRules(sessions=SessionConfig(windows=[
        SessionWindow(name="LONDON_LOCAL", tz="Europe/London", start="08:00", end="12:00")
    ]))
    # 2024-03-31 is when the UK moves to BST.
    frame = _hourly_frame("2024-03-29 00:00", 120)
    sessions = build_sessions(frame, rules)

    before = next(s for s in sessions.instances if s.start_time.date().isoformat() == "2024-03-29")
    after = next(s for s in sessions.instances if s.start_time.date().isoformat() == "2024-04-01")

    assert before.start_time.hour == 8      # GMT: 08:00 local == 08:00 UTC
    assert after.start_time.hour == 7       # BST: 08:00 local == 07:00 UTC


def test_disabled_windows_are_skipped():
    rules = SMCRules(sessions=SessionConfig(windows=[
        SessionWindow(name="ASIAN", start="00:00", end="07:00", enabled=False),
        SessionWindow(name="LONDON", start="07:00", end="12:00"),
    ]))
    sessions = build_sessions(_hourly_frame("2024-01-02 00:00", 24), rules)
    assert set(sessions.counts()) == {"LONDON"}


def test_invalid_window_time_is_rejected():
    with pytest.raises(ValueError, match="HH:MM"):
        SessionWindow(name="BAD", start="8am", end="12:00")
    with pytest.raises(ValueError, match="out of range"):
        SessionWindow(name="BAD", start="25:00", end="12:00")


def test_empty_frame_is_handled():
    sessions = build_sessions(make_frame([], []), DEFAULTS)
    assert sessions.instances == []
    assert sessions.session_at(0) == NO_SESSION


def test_to_frame():
    sessions = build_sessions(_hourly_frame("2024-01-02 00:00", 24), DEFAULTS)
    out = sessions.to_frame()
    assert len(out) == 4
    assert {"name", "high", "low", "range"} <= set(out.columns)
