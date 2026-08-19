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
    opens: list[float] | None = None,
    closes: list[float] | None = None,
    start: str = "2024-01-02 09:00",
    minutes: int = 5,
    symbol: str = "TESTm",
    timeframe: str = "M5",
) -> pd.DataFrame:
    """A canonical closed-bar frame with an exactly specified shape.

    Opens and closes default to the midpoint of each bar (no body, no
    displacement) so swing tests isolate geometry. Pass `opens`/`closes`
    explicitly when a test needs real bodies -- displacement scoring reads them.
    """
    from data.normalizer import normalize

    assert len(highs) == len(lows)
    if not highs:
        return normalize(pd.DataFrame(), symbol=symbol, timeframe=timeframe).frame
    index = pd.date_range(start=start, periods=len(highs), freq=f"{minutes}min")
    rows = []
    for i, (ts, high, low) in enumerate(zip(index, highs, lows)):
        assert high >= low, f"high {high} < low {low}"
        mid = (high + low) / 2
        open_ = opens[i] if opens is not None else mid
        close = closes[i] if closes is not None else mid
        assert low <= open_ <= high and low <= close <= high, f"bar {i} open/close outside range"
        rows.append(
            {
                "time": ts,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
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


def make_market_frame(
    n: int = 900,
    seed: int = 11,
    start: str = "2024-01-02 00:00",
    minutes: int = 5,
    symbol: str = "TESTm",
) -> pd.DataFrame:
    """A synthetic series that actually contains SMC structure.

    A plain random walk produces almost no fair value gaps, order blocks or
    sweeps -- its bars are all the same size and never overshoot. This
    generator alternates drift regimes, fires occasional impulse bars (which
    leave imbalances and origin candles), and prints stop-run wicks beyond
    recent extremes (which create sweeps). It is TEST SCAFFOLDING: never feed
    it to the probability engine.
    """
    import numpy as np

    rng = np.random.default_rng(seed)
    price = 2000.0
    drift = 0.0
    highs: list[float] = []
    lows: list[float] = []
    opens: list[float] = []
    closes: list[float] = []

    for i in range(n):
        if i % 60 == 0:                       # regime switch
            drift = rng.choice([-0.35, -0.15, 0.0, 0.15, 0.35])
        body = rng.normal(drift, 0.9)
        if rng.random() < 0.06:               # impulse: leaves gaps and OBs
            body *= rng.uniform(4.0, 7.0)

        open_ = price
        close = price + body
        upper = abs(rng.normal(0.35, 0.3))
        lower = abs(rng.normal(0.35, 0.3))
        if rng.random() < 0.05:               # stop run beyond the recent extreme
            if rng.random() < 0.5:
                upper += abs(rng.normal(2.0, 0.8))
            else:
                lower += abs(rng.normal(2.0, 0.8))

        high = max(open_, close) + upper
        low = min(open_, close) - lower
        highs.append(round(high, 3)); lows.append(round(low, 3))
        opens.append(round(open_, 3)); closes.append(round(close, 3))
        price = close

    return make_frame(highs, lows, opens=opens, closes=closes,
                      start=start, minutes=minutes, symbol=symbol)
