"""Synthetic data builders.

Every data-layer test runs against frames whose correct answer is known by
construction -- no reliance on a broker being reachable.
"""

from __future__ import annotations

import pandas as pd
import pytest

from config.settings import Settings, get_settings

# A deterministic "now" far after every synthetic bar, so bars are closed
# unless a test deliberately builds one in the future.
NOW = pd.Timestamp("2024-01-15 00:00", tz="UTC")


def make_raw_bars(
    n: int = 10,
    start: str = "2024-01-02 09:00",
    minutes: int = 5,
    first_price: float = 2000.0,
    step: float = 0.5,
    tz: str | None = None,
    time_column: str = "time",
) -> pd.DataFrame:
    """A clean, strictly rising raw frame in broker/naive time by default."""
    index = pd.date_range(start=start, periods=n, freq=f"{minutes}min", tz=tz)
    opens = [first_price + i * step for i in range(n)]
    rows = []
    for ts, o in zip(index, opens):
        c = o + step * 0.5
        rows.append(
            {
                time_column: ts,
                "open": round(o, 2),
                "high": round(max(o, c) + 0.30, 2),
                "low": round(min(o, c) - 0.30, 2),
                "close": round(c, 2),
                "tick_volume": 100,
                "spread": 12,
            }
        )
    return pd.DataFrame(rows)


def make_frame(
    highs: list[float],
    lows: list[float],
    *,
    start: str = "2024-01-02 09:00",
    minutes: int = 5,
    symbol: str = "TESTm",
    timeframe: str = "M5",
) -> pd.DataFrame:
    """A canonical closed-bar frame with an exactly specified high/low shape.

    Opens and closes are placed inside each bar's range, so the frame passes
    the normalizer's OHLC sanity checks.
    """
    from data.normalizer import normalize

    assert len(highs) == len(lows)
    if not highs:
        return normalize(pd.DataFrame(), symbol=symbol, timeframe=timeframe).frame
    index = pd.date_range(start=start, periods=len(highs), freq=f"{minutes}min")
    rows = []
    for ts, high, low in zip(index, highs, lows):
        assert high >= low, f"high {high} < low {low}"
        mid = (high + low) / 2
        rows.append(
            {
                "time": ts,
                "open": mid,
                "high": high,
                "low": low,
                "close": mid,
                "tick_volume": 100,
                "spread": 10,
            }
        )
    far_future = pd.Timestamp(index[-1], tz="UTC") + pd.Timedelta(days=365)
    return normalize(
        pd.DataFrame(rows), symbol=symbol, timeframe=timeframe, now=far_future
    ).frame


def zigzag(
    legs: list[float], bars_per_leg: int = 4, spread: float = 0.4, first: float = 100.0
) -> tuple[list[float], list[float]]:
    """Build highs/lows tracing a path through the given turning-point prices."""
    highs: list[float] = []
    lows: list[float] = []
    price = first
    for target in legs:
        for step in range(1, bars_per_leg + 1):
            level = price + (target - price) * step / bars_per_leg
            highs.append(round(level + spread, 4))
            lows.append(round(level - spread, 4))
        price = target
    return highs, lows


@pytest.fixture
def raw_bars() -> pd.DataFrame:
    return make_raw_bars()


@pytest.fixture
def now() -> pd.Timestamp:
    return NOW


@pytest.fixture
def tmp_settings(tmp_path) -> Settings:
    """Settings pointed at an isolated temp directory."""
    base = tmp_path / "data"
    s = get_settings().model_copy(
        update={
            "data_dir": base,
            "raw_dir": base / "raw",
            "cache_dir": base / "cache",
            "db_path": tmp_path / "database" / "smc.db",
            "broker_utc_offset_hours": 0.0,
        }
    )
    s.ensure_dirs()
    return s
