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

# Headerless layouts from known free sources. The key is what you pass to
# --format; the value is the column order the file actually has.
#
# histdata_mt: HistData.com "MetaTrader" export, e.g.
#     2026.07.01,00:00,3980.635,3980.855,3979.335,3979.335,0
#   Timestamps are New York time (EST/EDT, with DST), which is UTC-5 in winter
#   and UTC-4 in summer -- so ingest one month at a time with the right
#   --offset, or the daily 17:00 NY break lands in the wrong UTC hour and every
#   session feature shifts with it. The volume column is always 0 in this
#   format; it is real absence, and is stored as such rather than as a count.
LAYOUTS: dict[str, list[str]] = {
    "histdata_mt": ["date", "time", "open", "high", "low", "close", "tick_volume"],
    "ohlcv": ["timestamp", "open", "high", "low", "close", "tick_volume"],
    "ohlc": ["timestamp", "open", "high", "low", "close"],
}


def _looks_headerless(path: Path, separator: str) -> bool:
    """True when the first row is data, not column names.

    A header names its columns in words; a data row's OHLC fields parse as
    numbers. Checking the numeric fields is more reliable than guessing at
    header spellings, of which there are many.
    """
    with path.open("r", encoding="utf-8-sig", errors="replace") as fh:
        first = fh.readline().strip()
    if not first:
        return False
    fields = [f.strip().strip('"') for f in first.split(separator)]
    if len(fields) < 4:
        return False
    numeric = 0
    for field in fields:
        try:
            float(field)
            numeric += 1
        except ValueError:
            continue
    return numeric >= 4


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


def read_raw(path: str | Path, column_map: dict[str, str] | None = None,
             layout: str | list[str] | None = None) -> pd.DataFrame:
    """Read a file into a raw DataFrame, without applying the canonical schema.

    `layout` names the columns of a headerless file: either a key of `LAYOUTS`
    or an explicit list. Passing it also asserts the file IS headerless -- if a
    header turns up, that is a mismatch worth failing on rather than silently
    reading the column names as a price row.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"no such data file: {p}")

    suffix = p.suffix.lower()
    if suffix in _PARQUET_SUFFIXES:
        if layout:
            raise NormalizationError("layout applies to text files; parquet carries its own schema")
        df = pd.read_parquet(p)
        if isinstance(df.index, pd.DatetimeIndex):
            df = df.reset_index()
    elif suffix in _TEXT_SUFFIXES:
        separator = _sniff_separator(p)
        headerless = _looks_headerless(p, separator)

        if layout is not None:
            if isinstance(layout, str) and layout not in LAYOUTS:
                raise NormalizationError(
                    f"unknown layout {layout!r}. Known: {sorted(LAYOUTS)}"
                )
            names = LAYOUTS[layout] if isinstance(layout, str) else list(layout)
            if not headerless:
                raise NormalizationError(
                    f"{p} looks like it HAS a header, but layout={layout!r} was given. "
                    "Drop the layout, or check you named the right file."
                )
            df = pd.read_csv(p, sep=separator, encoding="utf-8-sig",
                             header=None, names=names)
        elif headerless:
            raise NormalizationError(
                f"{p} has no header row -- its first line is data. Say what the "
                f"columns are with --format (one of {sorted(LAYOUTS)}), e.g. "
                "--format histdata_mt for a HistData.com MetaTrader export."
            )
        else:
            df = pd.read_csv(p, sep=separator, encoding="utf-8-sig")
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
    layout: str | list[str] | None = None,
    on_duplicate: str = "last",
) -> NormalizedBars:
    """Read a file and normalize it in one call."""
    raw = read_raw(path, column_map=column_map, layout=layout)
    return normalize(
        raw,
        symbol=symbol,
        timeframe=timeframe,
        broker_utc_offset_hours=broker_utc_offset_hours,
        digits=digits,
        drop_forming=drop_forming,
        now=now,
        on_duplicate=on_duplicate,
    )
