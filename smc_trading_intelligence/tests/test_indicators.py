"""ATR primitives -- correctness and, above all, causality."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from common.indicators import true_range, wilder_atr
from tests.conftest import NOW, make_raw_bars
from data.normalizer import normalize


def _frame(n: int = 60) -> pd.DataFrame:
    return normalize(make_raw_bars(n), symbol="X", timeframe="M5", now=NOW).frame


def test_true_range_first_bar_uses_high_minus_low():
    frame = _frame(5)
    tr = true_range(frame)
    assert tr.iloc[0] == pytest.approx(frame["high"].iloc[0] - frame["low"].iloc[0])


def test_true_range_accounts_for_gaps_against_previous_close():
    frame = pd.DataFrame(
        {"open": [10.0, 20.0], "high": [11.0, 21.0], "low": [9.0, 19.5], "close": [10.5, 20.5]},
        index=pd.date_range("2024-01-01", periods=2, freq="5min", tz="UTC"),
    )
    # bar 2 gapped up: TR is 21.0 - 10.5, not 21.0 - 19.5
    assert true_range(frame).iloc[1] == pytest.approx(10.5)


def test_wilder_atr_is_nan_until_seeded_then_positive():
    atr = wilder_atr(_frame(40), period=14)
    assert atr.iloc[:13].isna().all()
    assert not np.isnan(atr.iloc[13])
    assert (atr.dropna() > 0).all()


def test_wilder_atr_seed_is_the_mean_of_the_first_true_ranges():
    frame = _frame(30)
    tr = true_range(frame)
    atr = wilder_atr(frame, period=14)
    assert atr.iloc[13] == pytest.approx(tr.iloc[:14].mean())


def test_wilder_atr_recursion_matches_the_definition():
    frame = _frame(30)
    tr, atr = true_range(frame), wilder_atr(frame, period=14)
    expected = atr.iloc[13] + (tr.iloc[14] - atr.iloc[13]) / 14
    assert atr.iloc[14] == pytest.approx(expected)


def test_atr_is_causal_under_truncation():
    """ATR at bar i must not change when future bars are removed."""
    frame = _frame(80)
    full = wilder_atr(frame, period=14)
    for cut in (20, 35, 50, 79):
        truncated = wilder_atr(frame.iloc[: cut + 1], period=14)
        pd.testing.assert_series_equal(truncated, full.iloc[: cut + 1])


def test_atr_shorter_than_period_is_all_nan():
    assert wilder_atr(_frame(5), period=14).isna().all()


def test_invalid_period_raises():
    with pytest.raises(ValueError):
        wilder_atr(_frame(20), period=0)
