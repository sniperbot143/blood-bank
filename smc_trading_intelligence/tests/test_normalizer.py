"""Normalizer contract tests. Each frame has a known-correct answer."""

from __future__ import annotations

import pandas as pd
import pytest

from data.normalizer import (
    CANONICAL_COLUMNS,
    NormalizationError,
    closed_bars,
    normalize,
    validate_frame,
)
from tests.conftest import NOW, make_raw_bars


def test_clean_input_yields_canonical_schema(raw_bars):
    result = normalize(raw_bars, symbol="XAUUSDm", timeframe="M5", now=NOW)
    frame = result.frame

    assert list(frame.columns) == CANONICAL_COLUMNS
    assert len(frame) == len(raw_bars)
    assert str(frame.index.tz) == "UTC"
    assert frame.index.name == "timestamp"
    assert frame.index.is_monotonic_increasing
    assert not frame.index.has_duplicates
    assert frame["is_closed"].all()
    assert not frame["gap_before"].any()
    assert (frame["symbol"] == "XAUUSDm").all()
    assert (frame["timeframe"] == "M5").all()
    validate_frame(frame)


def test_column_aliases_and_mt5_angle_brackets():
    raw = make_raw_bars(3).rename(
        columns={
            "time": "<DATE>",
            "open": "<OPEN>",
            "high": "<HIGH>",
            "low": "<LOW>",
            "close": "<CLOSE>",
            "tick_volume": "<TICKVOL>",
            "spread": "<SPREAD>",
        }
    )
    result = normalize(raw, symbol="X", timeframe="M5", now=NOW)
    assert len(result.frame) == 3


def test_epoch_seconds_are_treated_as_broker_time():
    raw = make_raw_bars(3, start="2024-01-02 09:00")
    raw["time"] = raw["time"].astype("datetime64[s]").astype("int64")  # MT5 epoch seconds

    result = normalize(raw, symbol="X", timeframe="M5", broker_utc_offset_hours=3, now=NOW)
    # 09:00 broker time with a +3h broker clock is 06:00 UTC.
    assert result.frame.index[0] == pd.Timestamp("2024-01-02 06:00", tz="UTC")


def test_naive_timestamps_shift_by_broker_offset():
    result = normalize(make_raw_bars(3), symbol="X", timeframe="M5",
                       broker_utc_offset_hours=2, now=NOW)
    assert result.frame.index[0] == pd.Timestamp("2024-01-02 07:00", tz="UTC")


def test_tz_aware_timestamps_ignore_the_offset():
    raw = make_raw_bars(3, tz="Europe/London")
    result = normalize(raw, symbol="X", timeframe="M5",
                       broker_utc_offset_hours=5, now=NOW)
    # January: London == UTC, and an absolute timestamp must not be shifted again.
    assert result.frame.index[0] == pd.Timestamp("2024-01-02 09:00", tz="UTC")


def test_duplicates_are_removed_keeping_the_last_revision():
    raw = make_raw_bars(5)
    dup = raw.iloc[[2]].copy()
    dup.loc[:, "tick_volume"] = 777          # a later, corrected revision of that bar
    raw = pd.concat([raw, dup], ignore_index=True)

    result = normalize(raw, symbol="X", timeframe="M5", now=NOW)
    assert result.report.duplicates_removed == 1
    assert len(result.frame) == 5
    assert result.frame.iloc[2]["tick_volume"] == 777


def test_unsorted_input_is_sorted():
    raw = make_raw_bars(6).sample(frac=1.0, random_state=7)
    result = normalize(raw, symbol="X", timeframe="M5", now=NOW)
    assert result.frame.index.is_monotonic_increasing


@pytest.mark.parametrize(
    "column,value",
    [("high", 1.0), ("low", 99999.0), ("close", -5.0), ("open", float("nan"))],
)
def test_impossible_ohlc_is_quarantined_not_repaired(column, value):
    raw = make_raw_bars(6)
    raw.loc[3, column] = value

    result = normalize(raw, symbol="X", timeframe="M5", now=NOW)
    assert result.report.bad_ohlc_quarantined == 1
    assert len(result.frame) == 5
    assert len(result.quarantined) == 1
    validate_frame(result.frame)


def test_missing_bars_are_recorded_not_filled():
    raw = pd.concat([make_raw_bars(5, start="2024-01-02 09:00"),
                     make_raw_bars(5, start="2024-01-02 11:00")], ignore_index=True)

    result = normalize(raw, symbol="X", timeframe="M5", now=NOW)
    assert len(result.frame) == 10  # nothing invented
    assert len(result.report.gaps) == 1

    gap = result.report.gaps[0]
    assert gap.kind == "other"          # midweek hole, needs review
    assert gap.missing_bars == 19       # 09:20 -> 11:00 is 20 steps, so 19 absent bars

    flagged = result.frame.index[result.frame["gap_before"]]
    assert list(flagged) == [pd.Timestamp("2024-01-02 11:00", tz="UTC")]


def test_weekend_gap_is_classified_as_weekend():
    friday = make_raw_bars(3, start="2024-01-05 20:45")   # Fri
    sunday = make_raw_bars(3, start="2024-01-07 22:00")   # Sun
    result = normalize(pd.concat([friday, sunday], ignore_index=True),
                       symbol="X", timeframe="M5", now=NOW)

    assert [g.kind for g in result.report.gaps] == ["weekend"]
    assert result.report.gaps_weekend == 1
    assert result.report.gaps_other == 0


def test_forming_bar_is_dropped_by_default():
    raw = make_raw_bars(4, start="2024-01-02 09:00")
    now = pd.Timestamp("2024-01-02 09:17", tz="UTC")  # 09:15 bar closes at 09:20

    result = normalize(raw, symbol="X", timeframe="M5", now=now)
    assert len(result.frame) == 3
    assert result.frame["is_closed"].all()
    assert result.report.forming_dropped == [pd.Timestamp("2024-01-02 09:15", tz="UTC")]


def test_forming_bar_can_be_kept_but_is_flagged():
    raw = make_raw_bars(4, start="2024-01-02 09:00")
    now = pd.Timestamp("2024-01-02 09:17", tz="UTC")

    result = normalize(raw, symbol="X", timeframe="M5", now=now, drop_forming=False)
    assert len(result.frame) == 4
    assert not result.frame["is_closed"].iloc[-1]
    assert len(closed_bars(result.frame)) == 3


def test_bar_closing_exactly_now_counts_as_closed():
    raw = make_raw_bars(1, start="2024-01-02 09:00")
    result = normalize(raw, symbol="X", timeframe="M5",
                       now=pd.Timestamp("2024-01-02 09:05", tz="UTC"))
    assert len(result.frame) == 1


def test_digits_rounding_is_applied():
    raw = make_raw_bars(2)
    raw.loc[:, "close"] = 2000.123456
    result = normalize(raw, symbol="X", timeframe="M5", digits=2, now=NOW)
    assert result.frame["close"].iloc[0] == 2000.12


def test_missing_required_column_raises():
    raw = make_raw_bars(3).drop(columns=["close"])
    with pytest.raises(NormalizationError, match="missing required column"):
        normalize(raw, symbol="X", timeframe="M5", now=NOW)


def test_unparseable_timestamp_raises():
    raw = make_raw_bars(3)
    raw["time"] = raw["time"].astype(str)
    raw.loc[1, "time"] = "not-a-date"
    with pytest.raises(NormalizationError, match="could not be parsed"):
        normalize(raw, symbol="X", timeframe="M5", now=NOW)


def test_empty_input_produces_valid_empty_frame():
    result = normalize(pd.DataFrame(), symbol="X", timeframe="M5", now=NOW)
    assert result.frame.empty
    validate_frame(result.frame)


def test_unknown_timeframe_raises():
    with pytest.raises(ValueError, match="Unknown timeframe"):
        normalize(make_raw_bars(2), symbol="X", timeframe="M7", now=NOW)


def test_validate_frame_rejects_unsorted_index(raw_bars):
    frame = normalize(raw_bars, symbol="X", timeframe="M5", now=NOW).frame
    with pytest.raises(NormalizationError, match="not sorted"):
        validate_frame(frame.iloc[::-1])


def test_validate_frame_rejects_naive_index(raw_bars):
    frame = normalize(raw_bars, symbol="X", timeframe="M5", now=NOW).frame
    naive = frame.copy()
    naive.index = naive.index.tz_localize(None)
    with pytest.raises(NormalizationError, match="UTC"):
        validate_frame(naive)


def test_report_summary_is_human_readable(raw_bars):
    report = normalize(raw_bars, symbol="XAUUSDm", timeframe="M5", now=NOW).report
    text = report.summary()
    assert "XAUUSDm M5" in text and "duplicates removed: 0" in text


# --------------------------------------------------------- duplicate policy

def _interleaved_pair() -> pd.DataFrame:
    """The HistData artifact: a real bar and a flat filler share a stamp.

    The filler lands on either side of the real row depending on the minute,
    so position alone cannot tell you which to keep.
    """
    return pd.DataFrame({
        "timestamp": ["2026-07-01 07:43", "2026-07-01 07:43",
                      "2026-07-01 07:44", "2026-07-01 07:44"],
        "open":  [4024.725, 4031.645, 4031.245, 4031.485],
        "high":  [4031.215, 4031.645, 4031.245, 4031.615],
        "low":   [4024.575, 4031.645, 4031.245, 4028.755],
        "close": [4031.065, 4031.645, 4031.245, 4028.965],
        "tick_volume": [0, 0, 0, 0],
    })


def test_keep_last_can_discard_the_real_bar_for_a_flat_one():
    """Documents the trap: position-based dedup is a coin flip on this data."""
    result = normalize(_interleaved_pair(), symbol="XAUUSD", timeframe="M1",
                       drop_forming=False, on_duplicate="last")
    frame = result.frame

    assert result.report.duplicates_removed == 2
    first = frame.iloc[0]
    assert first["high"] == first["low"]          # the flat filler survived


def test_widest_keeps_the_bar_that_carries_information():
    result = normalize(_interleaved_pair(), symbol="XAUUSD", timeframe="M1",
                       drop_forming=False, on_duplicate="widest")
    frame = result.frame

    assert result.report.duplicates_removed == 2
    assert len(frame) == 2
    assert not (frame["high"] == frame["low"]).any()
    assert frame.iloc[0]["high"] == pytest.approx(4031.215)
    assert frame.iloc[1]["low"] == pytest.approx(4028.755)


def test_first_keeps_the_earlier_row():
    result = normalize(_interleaved_pair(), symbol="XAUUSD", timeframe="M1",
                       drop_forming=False, on_duplicate="first")
    assert result.frame.iloc[0]["high"] == pytest.approx(4031.215)


def test_every_policy_leaves_a_unique_sorted_index():
    for policy in ("first", "last", "widest"):
        frame = normalize(_interleaved_pair(), symbol="XAUUSD", timeframe="M1",
                          drop_forming=False, on_duplicate=policy).frame
        assert not frame.index.has_duplicates, policy
        assert frame.index.is_monotonic_increasing, policy


def test_an_unknown_duplicate_policy_is_refused():
    with pytest.raises(NormalizationError, match="unknown duplicate policy"):
        normalize(_interleaved_pair(), symbol="XAUUSD", timeframe="M1",
                  drop_forming=False, on_duplicate="whichever")
