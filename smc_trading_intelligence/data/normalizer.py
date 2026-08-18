"""Turn "whatever the broker gave us" into one trustworthy candle table.

This is the single gate every later module reads through. It enforces the
canonical schema from docs/ARCHITECTURE.md section 4:

  * UTC timestamp index (bar OPEN time), unique and monotonic
  * OHLC sanity (bad bars are quarantined, never silently repaired)
  * duplicates removed
  * missing bars recorded in a gap map -- never forward-filled
  * `is_closed` flag; the forming bar is dropped by default
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

import numpy as np
import pandas as pd

from config.settings import get_timeframe

# Canonical column order. `timestamp` is the index, not a column.
OHLC_COLUMNS = ["open", "high", "low", "close"]
CANONICAL_COLUMNS = [
    "open",
    "high",
    "low",
    "close",
    "tick_volume",
    "real_volume",
    "spread",
    "symbol",
    "timeframe",
    "is_closed",
    "gap_before",
]

# Accepted inbound names -> canonical name. Lower-cased and stripped of
# <angle brackets> (MT5 exports) and spaces/underscores before lookup.
COLUMN_ALIASES: dict[str, str] = {
    "time": "timestamp",
    "date": "timestamp",
    "datetime": "timestamp",
    "timestamp": "timestamp",
    "gmttime": "timestamp",
    "servertime": "timestamp",
    "o": "open",
    "open": "open",
    "h": "high",
    "high": "high",
    "l": "low",
    "low": "low",
    "c": "close",
    "close": "close",
    "adjclose": "close",
    "vol": "tick_volume",
    "volume": "tick_volume",
    "tickvol": "tick_volume",
    "tickvolume": "tick_volume",
    "realvolume": "real_volume",
    "realvol": "real_volume",
    "spread": "spread",
}


class NormalizationError(ValueError):
    """Raised when the input cannot be made to fit the canonical schema."""


@dataclass
class GapRecord:
    """One stretch of missing bars."""

    previous: pd.Timestamp
    current: pd.Timestamp
    missing_bars: int
    kind: str  # "weekend" | "other"


@dataclass
class NormalizationReport:
    symbol: str
    timeframe: str
    rows_in: int = 0
    rows_out: int = 0
    duplicates_removed: int = 0
    bad_ohlc_quarantined: int = 0
    forming_dropped: list[pd.Timestamp] = field(default_factory=list)
    gaps: list[GapRecord] = field(default_factory=list)
    first_timestamp: pd.Timestamp | None = None
    last_timestamp: pd.Timestamp | None = None
    broker_utc_offset_hours: float = 0.0

    @property
    def gaps_weekend(self) -> int:
        return sum(1 for g in self.gaps if g.kind == "weekend")

    @property
    def gaps_other(self) -> int:
        return sum(1 for g in self.gaps if g.kind == "other")

    def summary(self) -> str:
        span = "empty"
        if self.first_timestamp is not None and self.last_timestamp is not None:
            span = (
                f"{self.first_timestamp:%Y-%m-%d %H:%M} -> "
                f"{self.last_timestamp:%Y-%m-%d %H:%M} UTC"
            )
        lines = [
            f"{self.symbol} {self.timeframe} | {self.rows_out:,} bars | {span}",
            f"duplicates removed: {self.duplicates_removed} | "
            f"bad OHLC quarantined: {self.bad_ohlc_quarantined} | "
            f"gaps: {len(self.gaps)} (weekend {self.gaps_weekend}, other {self.gaps_other})",
        ]
        for ts in self.forming_dropped:
            lines.append(f"forming bar dropped: {ts:%Y-%m-%d %H:%M} UTC")
        return "\n".join(lines)


@dataclass
class NormalizedBars:
    """Result of a normalization run."""

    frame: pd.DataFrame
    quarantined: pd.DataFrame
    report: NormalizationReport


def _canonical_name(raw: str) -> str:
    key = str(raw).strip().lower().strip("<>").replace(" ", "").replace("_", "")
    return COLUMN_ALIASES.get(key, key)


def _rename_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [_canonical_name(c) for c in out.columns]
    # A repeated canonical name (e.g. both "vol" and "volume" present) would
    # produce ambiguous data; keep the first and drop the rest.
    return out.loc[:, ~out.columns.duplicated()]


def _to_utc_index(
    values: pd.Series, *, broker_utc_offset_hours: float
) -> pd.DatetimeIndex:
    """Convert a timestamp column to a true UTC DatetimeIndex.

    Three input shapes are handled:
      * tz-aware datetimes -> converted to UTC, offset ignored (already absolute)
      * naive datetimes    -> treated as broker server time, shifted to UTC
      * integers/floats    -> epoch seconds in broker server time (MT5's format)
    """
    if pd.api.types.is_numeric_dtype(values):
        parsed = pd.to_datetime(values, unit="s", errors="coerce")
    else:
        parsed = pd.to_datetime(values, errors="coerce", format="mixed", utc=False)

    if parsed.isna().any():
        bad = int(parsed.isna().sum())
        raise NormalizationError(f"{bad} timestamp value(s) could not be parsed")

    idx = pd.DatetimeIndex(parsed)
    if idx.tz is not None:
        return idx.tz_convert("UTC")
    return (idx - timedelta(hours=broker_utc_offset_hours)).tz_localize("UTC")


def _bad_ohlc_mask(df: pd.DataFrame, *, allow_nonpositive: bool) -> pd.Series:
    """True where a row's OHLC is impossible and must be quarantined."""
    o, h, l, c = (df[col] for col in OHLC_COLUMNS)
    bad = df[OHLC_COLUMNS].isna().any(axis=1)
    if not allow_nonpositive:
        bad |= (df[OHLC_COLUMNS] <= 0).any(axis=1)
    eps = 1e-12
    bad |= h < l - eps
    bad |= h < o.combine(c, max) - eps
    bad |= l > o.combine(c, min) + eps
    return bad


def _classify_gap(previous: pd.Timestamp, current: pd.Timestamp) -> str:
    """A gap spanning a Saturday is the normal weekend close."""
    day = (previous + timedelta(days=1)).normalize()
    while day < current:
        if day.weekday() == 5:  # Saturday
            return "weekend"
        day += timedelta(days=1)
    return "other"


def _build_gap_map(index: pd.DatetimeIndex, step: timedelta) -> tuple[np.ndarray, list[GapRecord]]:
    gap_before = np.zeros(len(index), dtype=bool)
    gaps: list[GapRecord] = []
    if len(index) < 2:
        return gap_before, gaps

    deltas = index[1:] - index[:-1]
    step_td = pd.Timedelta(step)
    for pos in np.flatnonzero(deltas > step_td):
        i = int(pos) + 1
        gap_before[i] = True
        missing = int(deltas[pos] / step_td) - 1
        gaps.append(
            GapRecord(
                previous=index[i - 1],
                current=index[i],
                missing_bars=missing,
                kind=_classify_gap(index[i - 1], index[i]),
            )
        )
    return gap_before, gaps


def normalize(
    raw: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
    broker_utc_offset_hours: float = 0.0,
    digits: int | None = None,
    drop_forming: bool = True,
    now: pd.Timestamp | None = None,
    allow_nonpositive_prices: bool = False,
) -> NormalizedBars:
    """Normalize a raw candle frame into the canonical schema.

    `now` (UTC) decides which bars are closed; it is injectable so tests are
    deterministic. A bar opening at T on an M5 chart is closed once T+5min has
    passed -- never before.
    """
    tf = get_timeframe(timeframe)
    report = NormalizationReport(
        symbol=symbol,
        timeframe=tf.name,
        rows_in=len(raw),
        broker_utc_offset_hours=broker_utc_offset_hours,
    )

    if raw is None or len(raw) == 0:
        empty = _empty_frame(symbol, tf.name)
        return NormalizedBars(frame=empty, quarantined=empty.copy(), report=report)

    df = _rename_columns(raw.reset_index() if raw.index.name else raw.copy())
    df = _rename_columns(df)

    missing = [c for c in ["timestamp", *OHLC_COLUMNS] if c not in df.columns]
    if missing:
        raise NormalizationError(
            f"input is missing required column(s): {missing}. "
            f"Columns seen: {sorted(df.columns)}"
        )

    df.index = _to_utc_index(df["timestamp"], broker_utc_offset_hours=broker_utc_offset_hours)
    df.index.name = "timestamp"
    df = df.drop(columns=["timestamp"])

    for col in OHLC_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col, default in (("tick_volume", 0), ("real_volume", 0), ("spread", 0)):
        df[col] = (
            pd.to_numeric(df[col], errors="coerce").fillna(0)
            if col in df.columns
            else default
        )

    # Sort first so "keep last" on duplicates means the latest-known revision.
    df = df.sort_index(kind="stable")

    dup_mask = df.index.duplicated(keep="last")
    report.duplicates_removed = int(dup_mask.sum())
    df = df[~dup_mask]

    bad_mask = _bad_ohlc_mask(df, allow_nonpositive=allow_nonpositive_prices)
    report.bad_ohlc_quarantined = int(bad_mask.sum())
    quarantined = df[bad_mask].copy()
    df = df[~bad_mask]

    if digits is not None:
        df[OHLC_COLUMNS] = df[OHLC_COLUMNS].round(int(digits))

    now_utc = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    if now_utc.tzinfo is None:
        now_utc = now_utc.tz_localize("UTC")
    is_closed = (df.index + tf.delta) <= now_utc
    df["is_closed"] = is_closed

    if drop_forming and (~is_closed).any():
        report.forming_dropped = list(df.index[~is_closed])
        df = df[is_closed]

    gap_before, gaps = _build_gap_map(pd.DatetimeIndex(df.index), tf.delta)
    df["gap_before"] = gap_before
    report.gaps = gaps

    df["symbol"] = symbol
    df["timeframe"] = tf.name
    df = _enforce_dtypes(df)[CANONICAL_COLUMNS]

    report.rows_out = len(df)
    if len(df):
        report.first_timestamp = df.index[0]
        report.last_timestamp = df.index[-1]

    return NormalizedBars(frame=df, quarantined=quarantined, report=report)


def _enforce_dtypes(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in OHLC_COLUMNS:
        out[col] = out[col].astype("float64")
    for col in ("tick_volume", "real_volume"):
        out[col] = out[col].astype("int64")
    out["spread"] = out["spread"].astype("int32")
    out["symbol"] = out["symbol"].astype("category")
    out["timeframe"] = out["timeframe"].astype("category")
    out["is_closed"] = out["is_closed"].astype("bool")
    out["gap_before"] = out["gap_before"].astype("bool")
    return out


def _empty_frame(symbol: str, timeframe: str) -> pd.DataFrame:
    idx = pd.DatetimeIndex([], tz="UTC", name="timestamp")
    frame = pd.DataFrame(
        {
            "open": pd.Series(dtype="float64"),
            "high": pd.Series(dtype="float64"),
            "low": pd.Series(dtype="float64"),
            "close": pd.Series(dtype="float64"),
            "tick_volume": pd.Series(dtype="int64"),
            "real_volume": pd.Series(dtype="int64"),
            "spread": pd.Series(dtype="int32"),
            "symbol": pd.Series(dtype="object"),
            "timeframe": pd.Series(dtype="object"),
            "is_closed": pd.Series(dtype="bool"),
            "gap_before": pd.Series(dtype="bool"),
        },
        index=idx,
    )
    frame["symbol"] = frame["symbol"].astype("category")
    frame["timeframe"] = frame["timeframe"].astype("category")
    return frame[CANONICAL_COLUMNS]


def validate_frame(df: pd.DataFrame) -> None:
    """Assert the canonical contract. Raises NormalizationError on violation.

    Cheap enough to call at the top of any consumer; the whole point of the
    data layer is that everything downstream can trust these five facts.
    """
    if list(df.columns) != CANONICAL_COLUMNS:
        raise NormalizationError(
            f"column mismatch.\nexpected: {CANONICAL_COLUMNS}\ngot:      {list(df.columns)}"
        )
    if not isinstance(df.index, pd.DatetimeIndex):
        raise NormalizationError("index must be a DatetimeIndex")
    if df.index.tz is None or str(df.index.tz) != "UTC":
        raise NormalizationError(f"index must be tz-aware UTC, got {df.index.tz}")
    if df.index.has_duplicates:
        raise NormalizationError("index contains duplicate timestamps")
    if not df.index.is_monotonic_increasing:
        raise NormalizationError("index is not sorted ascending")
    if len(df) and _bad_ohlc_mask(df, allow_nonpositive=True).any():
        raise NormalizationError("frame contains impossible OHLC rows")


def closed_bars(df: pd.DataFrame) -> pd.DataFrame:
    """The only view the SMC engine is ever allowed to see."""
    return df[df["is_closed"]]
