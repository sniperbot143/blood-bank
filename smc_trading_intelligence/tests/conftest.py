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
