"""Load candles from CSV / TSV / Parquet files.

This path exists so the whole system works with zero network access and on
any OS -- the MT5 python package is Windows-only, but a CSV export from it is
not. Handles the MT5 "Export bars" format (tab separated, <DATE> <TIME>
columns) as well as generic OHLCV files.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd

from data.normalizer import NormalizationError, NormalizedBars, normalize

_TEXT_SUFFIXES = {".csv", ".tsv", ".txt"}
_PARQUET_SUFFIXES = {".parquet", ".pq"}


def _sniff_separator(path: Path) -> str:
    with path.open("r", encoding="utf-8-sig", errors="replace") as fh:
        sample = fh.read(8192)
    if not sample.strip():
        raise NormalizationError(f"{path} is empty")
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        # Fall back to the most common count in the header line.
        header = sample.splitlines()[0]
        return max(",;\t|", key=header.count)


def _combine_date_time(df: pd.DataFrame) -> pd.DataFrame:
    """MT5 exports split the stamp across <DATE> and <TIME>; rejoin them."""
    lowered = {str(c).strip().lower().strip("<>"): c for c in df.columns}
    date_col, time_col = lowered.get("date"), lowered.get("time")
    if date_col is None or time_col is None:
        return df
    out = df.copy()
    out["timestamp"] = (
        out[date_col].astype(str).str.strip() + " " + out[time_col].astype(str).str.strip()
    )
    return out.drop(columns=[date_col, time_col])


def read_raw(path: str | Path, column_map: dict[str, str] | None = None) -> pd.DataFrame:
    """Read a file into a raw DataFrame, without applying the canonical schema."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"no such data file: {p}")

    suffix = p.suffix.lower()
    if suffix in _PARQUET_SUFFIXES:
        df = pd.read_parquet(p)
        if isinstance(df.index, pd.DatetimeIndex):
            df = df.reset_index()
    elif suffix in _TEXT_SUFFIXES:
        df = pd.read_csv(p, sep=_sniff_separator(p), encoding="utf-8-sig")
    else:
        raise NormalizationError(
            f"unsupported file type {suffix!r}. Supported: "
            f"{sorted(_TEXT_SUFFIXES | _PARQUET_SUFFIXES)}"
        )

    if column_map:
        df = df.rename(columns=column_map)
    return _combine_date_time(df)


def load_csv(
    path: str | Path,
    *,
    symbol: str,
    timeframe: str,
    broker_utc_offset_hours: float = 0.0,
    digits: int | None = None,
    drop_forming: bool = True,
    now: pd.Timestamp | None = None,
    column_map: dict[str, str] | None = None,
) -> NormalizedBars:
    """Read a file and normalize it in one call."""
    raw = read_raw(path, column_map=column_map)
    return normalize(
        raw,
        symbol=symbol,
        timeframe=timeframe,
        broker_utc_offset_hours=broker_utc_offset_hours,
        digits=digits,
        drop_forming=drop_forming,
        now=now,
    )
