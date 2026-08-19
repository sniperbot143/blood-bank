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


def directional_movement(frame: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Wilder's +DI / -DI / ADX. Causal: bar i uses only bars 0..i."""
    high = frame["high"].to_numpy("float64")
    low = frame["low"].to_numpy("float64")
    n = len(frame)
    out = pd.DataFrame(
        {"plus_di": np.full(n, np.nan), "minus_di": np.full(n, np.nan),
         "adx": np.full(n, np.nan)},
        index=frame.index,
    )
    if n < period * 2:
        return out

    up = np.diff(high, prepend=high[0])
    down = -np.diff(low, prepend=low[0])
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = true_range(frame).to_numpy("float64")

    def _smooth(values: np.ndarray) -> np.ndarray:
        smoothed = np.full(n, np.nan)
        smoothed[period - 1] = values[:period].sum()
        for i in range(period, n):
            smoothed[i] = smoothed[i - 1] - smoothed[i - 1] / period + values[i]
        return smoothed

    tr_s, plus_s, minus_s = _smooth(tr), _smooth(plus_dm), _smooth(minus_dm)
    with np.errstate(invalid="ignore", divide="ignore"):
        plus_di = 100.0 * plus_s / tr_s
        minus_di = 100.0 * minus_s / tr_s
        dx = 100.0 * np.abs(plus_di - minus_di) / (plus_di + minus_di)

    adx = np.full(n, np.nan)
    first = period * 2 - 2
    if first < n:
        window = dx[period - 1:first + 1]
        if np.isfinite(window).all():
            adx[first] = window.mean()
            for i in range(first + 1, n):
                adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period

    out["plus_di"], out["minus_di"], out["adx"] = plus_di, minus_di, adx
    return out


def rolling_percentile(series: pd.Series, window: int, min_periods: int | None = None) -> pd.Series:
    """Percentile rank of each value within its own trailing window (0..1)."""
    return series.rolling(window, min_periods=min_periods or max(10, window // 10)).apply(
        lambda w: float((w[:-1] <= w[-1]).mean()) if len(w) > 1 else float("nan"),
        raw=True,
    )
