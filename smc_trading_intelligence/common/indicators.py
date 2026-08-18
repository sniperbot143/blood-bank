"""Causal indicator primitives.

Every function here is strictly causal: the value at bar i depends only on
bars 0..i. That property is what lets the swing engine (and everything after
it) claim to be non-repainting, so it is asserted by a test, not assumed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def true_range(frame: pd.DataFrame) -> pd.Series:
    """Wilder's True Range. TR[0] falls back to high-low (no previous close)."""
    high = frame["high"].to_numpy(dtype="float64")
    low = frame["low"].to_numpy(dtype="float64")
    close = frame["close"].to_numpy(dtype="float64")

    tr = np.empty(len(frame), dtype="float64")
    if len(frame) == 0:
        return pd.Series(tr, index=frame.index, name="true_range")

    tr[0] = high[0] - low[0]
    if len(frame) > 1:
        prev_close = close[:-1]
        tr[1:] = np.maximum(
            high[1:] - low[1:],
            np.maximum(np.abs(high[1:] - prev_close), np.abs(low[1:] - prev_close)),
        )
    return pd.Series(tr, index=frame.index, name="true_range")


def wilder_atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder's ATR: SMA seed over the first `period` bars, then recursive
    smoothing. NaN until the seed is available -- never back-filled, because a
    fabricated early ATR would silently change every ATR-scaled threshold.
    """
    if period < 1:
        raise ValueError("ATR period must be >= 1")

    tr = true_range(frame).to_numpy(dtype="float64")
    n = len(tr)
    atr = np.full(n, np.nan, dtype="float64")
    if n < period:
        return pd.Series(atr, index=frame.index, name="atr")

    atr[period - 1] = tr[:period].mean()
    inv = 1.0 / period
    for i in range(period, n):
        atr[i] = atr[i - 1] + (tr[i] - atr[i - 1]) * inv
    return pd.Series(atr, index=frame.index, name="atr")


def rolling_median_causal(series: pd.Series, window: int, min_periods: int | None = None) -> pd.Series:
    """Rolling median over the trailing `window` bars, inclusive of the current one."""
    return series.rolling(window=window, min_periods=min_periods or max(2, window // 4)).median()
