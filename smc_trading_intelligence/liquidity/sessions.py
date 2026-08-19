"""Trading sessions: which bars belong to which session, and each one's range.

Windows are defined in a named timezone (docs/SMC_DEFINITIONS.md §13), so
`Europe/London` windows shift correctly across DST while `UTC` windows stay
fixed. A session that has not finished yet is never treated as complete -- its
high and low are still moving, so they cannot be liquidity.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from config.smc_rules import DEFAULT_RULES, SessionConfig, SessionWindow, SMCRules

NO_SESSION = ""


@dataclass(frozen=True)
class SessionInstance:
    """One occurrence of one session -- e.g. London on 2024-03-14."""

    name: str
    label: str                 # "LONDON 2024-03-14"
    start_index: int
    end_index: int             # inclusive; the last bar inside the window
    start_time: pd.Timestamp
    end_time: pd.Timestamp
    open: float
    high: float
    low: float
    close: float
    high_index: int
    low_index: int

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def bars(self) -> int:
        return self.end_index - self.start_index + 1

    def is_complete_at(self, index: int) -> bool:
        """A session is only usable once its last bar is in the past."""
        return index > self.end_index


@dataclass
class SessionSeries:
    """All session instances over one frame."""

    instances: list[SessionInstance] = field(default_factory=list)
    labels: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=object))
    n_bars: int = 0
    config: SessionConfig = field(default_factory=SessionConfig)

    def session_at(self, index: int) -> str:
        if not self.n_bars:
            return NO_SESSION
        return self.labels[max(0, min(index, self.n_bars - 1))]

    def completed_before(self, index: int) -> list[SessionInstance]:
        return [s for s in self.instances if s.is_complete_at(index)]

    def last_completed(self, name: str, index: int) -> SessionInstance | None:
        for instance in reversed(self.instances):
            if instance.name == name and instance.is_complete_at(index):
                return instance
        return None

    def current(self, index: int) -> SessionInstance | None:
        for instance in self.instances:
            if instance.start_index <= index <= instance.end_index:
                return instance
        return None

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for instance in self.instances:
            counts[instance.name] = counts.get(instance.name, 0) + 1
        return counts

    def to_frame(self) -> pd.DataFrame:
        rows = [
            {
                "name": s.name,
                "label": s.label,
                "start_time": s.start_time,
                "end_time": s.end_time,
                "bars": s.bars,
                "high": s.high,
                "low": s.low,
                "range": s.range,
            }
            for s in self.instances
        ]
        return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["name", "label"])


def _in_window(minutes: np.ndarray, window: SessionWindow) -> np.ndarray:
    start, end = window.start_minutes, window.end_minutes
    if window.wraps_midnight:
        return (minutes >= start) | (minutes < end)
    return (minutes >= start) & (minutes < end)


def _local_parts(index: pd.DatetimeIndex, tz: str) -> tuple[np.ndarray, np.ndarray]:
    """Local minute-of-day and a session-day key for a timezone.

    For a window that wraps midnight, the day key rolls at the window start so
    one overnight session is not split into two.
    """
    local = index.tz_convert(tz)
    minutes = (local.hour * 60 + local.minute).to_numpy()
    # Drop the tz to get a plain calendar-day key; the local date is what
    # names the session ("LONDON 2024-03-14"), not the UTC date.
    dates = local.tz_localize(None).normalize().to_numpy()
    return minutes, dates


def build_sessions(
    frame: pd.DataFrame,
    rules: SMCRules = DEFAULT_RULES,
) -> SessionSeries:
    """Assign every bar to a session and summarise each session instance."""
    config = rules.sessions
    n = len(frame)
    series = SessionSeries(n_bars=n, config=config)
    if n == 0:
        return series

    labels = np.full(n, NO_SESSION, dtype=object)
    highs = frame["high"].to_numpy("float64")
    lows = frame["low"].to_numpy("float64")
    opens = frame["open"].to_numpy("float64")
    closes = frame["close"].to_numpy("float64")

    for window in config.active:
        minutes, dates = _local_parts(frame.index, window.tz)
        inside = _in_window(minutes, window)
        if not inside.any():
            continue

        # Group by session-day. For a wrapping window the post-midnight bars
        # belong to the previous day's session.
        day_keys = dates.copy()
        if window.wraps_midnight:
            day_keys = np.where(minutes < window.end_minutes,
                                dates - np.timedelta64(1, "D"), dates)

        positions = np.flatnonzero(inside)
        current_key = None
        block: list[int] = []

        def flush(block_positions: list[int], key) -> None:
            if not block_positions:
                return
            start, end = block_positions[0], block_positions[-1]
            block_slice = slice(start, end + 1)
            high_offset = int(np.argmax(highs[block_slice]))
            low_offset = int(np.argmin(lows[block_slice]))
            day = pd.Timestamp(key).date()
            series.instances.append(
                SessionInstance(
                    name=window.name,
                    label=f"{window.name} {day}",
                    start_index=start,
                    end_index=end,
                    start_time=frame.index[start],
                    end_time=frame.index[end],
                    open=float(opens[start]),
                    high=float(highs[block_slice].max()),
                    low=float(lows[block_slice].min()),
                    close=float(closes[end]),
                    high_index=start + high_offset,
                    low_index=start + low_offset,
                )
            )

        for pos in positions:
            key = day_keys[pos]
            labels[pos] = window.name
            if current_key is None or key != current_key or (block and pos != block[-1] + 1):
                flush(block, current_key)
                block, current_key = [int(pos)], key
            else:
                block.append(int(pos))
        flush(block, current_key)

    series.instances.sort(key=lambda s: (s.start_index, s.name))
    series.labels = labels
    return series
