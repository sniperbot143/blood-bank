"""Local parquet cache: one file per symbol/timeframe, plus a manifest.

Free, local, incremental. Re-ingesting appends only what is new and leaves
existing rows untouched, so a 200k-bar history is downloaded once.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from pydantic import BaseModel

from config.settings import SCHEMA_VERSION, Settings, get_settings, get_timeframe
from data.normalizer import (
    CANONICAL_COLUMNS,
    NormalizationError,
    _build_gap_map,
    _enforce_dtypes,
    validate_frame,
)

_HASH_COLUMNS = ["open", "high", "low", "close", "tick_volume", "real_volume", "spread"]


class CacheManifest(BaseModel):
    """What is in the cache file, and under what assumptions it was built."""

    symbol: str
    timeframe: str
    schema_version: int = SCHEMA_VERSION
    rows: int
    first_timestamp: datetime | None = None
    last_timestamp: datetime | None = None
    digits: int | None = None
    broker_utc_offset_hours: float = 0.0
    content_hash: str = ""
    source: str = "unknown"
    updated_at: datetime | None = None


def content_hash(frame: pd.DataFrame) -> str:
    """Stable hash of the price content (index + OHLCV), for change detection."""
    if frame.empty:
        return hashlib.sha256(b"").hexdigest()
    hashed = pd.util.hash_pandas_object(frame[_HASH_COLUMNS], index=True)
    return hashlib.sha256(hashed.to_numpy().tobytes()).hexdigest()


def merge_frames(existing: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """Union two canonical frames; on an overlapping timestamp the new row wins.

    The broker can revise the most recent bars (final tick volume, corrected
    spread), so "new wins" is right -- but only for bars we actually re-fetched.
    """
    if existing.empty:
        return new.copy()
    if new.empty:
        return existing.copy()

    combined = pd.concat([existing, new])
    combined = combined[~combined.index.duplicated(keep="last")]
    combined = combined.sort_index(kind="stable")

    # The gap map must describe the merged series, not either half: a hole at
    # the join between two ingests is exactly the kind of thing that would
    # otherwise go unnoticed.
    tf = get_timeframe(str(combined["timeframe"].iloc[0]))
    gap_before, _ = _build_gap_map(pd.DatetimeIndex(combined.index), tf.delta)
    combined["gap_before"] = gap_before

    return _enforce_dtypes(combined)[CANONICAL_COLUMNS]


def read_manifest(symbol: str, timeframe: str, settings: Settings | None = None) -> CacheManifest | None:
    s = settings or get_settings()
    path = s.manifest_path(symbol, timeframe)
    if not path.exists():
        return None
    return CacheManifest.model_validate_json(path.read_text(encoding="utf-8"))


def read_bars(
    symbol: str,
    timeframe: str,
    settings: Settings | None = None,
    *,
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Read cached bars. Raises FileNotFoundError if nothing is cached yet."""
    s = settings or get_settings()
    tf = get_timeframe(timeframe).name
    path = s.cache_path(symbol, tf)
    if not path.exists():
        raise FileNotFoundError(
            f"no cache for {symbol} {tf} at {path}. Run:\n"
            f"  python main.py ingest --symbol {symbol} --tf {tf}"
        )

    frame = pd.read_parquet(path)
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise NormalizationError(f"corrupt cache file {path}: index is not datetime")
    frame.index.name = "timestamp"
    frame = _enforce_dtypes(frame)[CANONICAL_COLUMNS]

    if start is not None:
        frame = frame[frame.index >= pd.Timestamp(start).tz_convert("UTC")]
    if end is not None:
        frame = frame[frame.index <= pd.Timestamp(end).tz_convert("UTC")]
    return frame


def write_bars(
    frame: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
    settings: Settings | None = None,
    digits: int | None = None,
    source: str = "unknown",
    merge: bool = True,
) -> CacheManifest:
    """Validate, merge with any existing cache, and persist + manifest."""
    s = settings or get_settings()
    tf = get_timeframe(timeframe).name
    validate_frame(frame)

    path = s.cache_path(symbol, tf)
    path.parent.mkdir(parents=True, exist_ok=True)

    combined = frame
    if merge and path.exists():
        combined = merge_frames(read_bars(symbol, tf, s), frame)
        validate_frame(combined)

    combined.to_parquet(path, engine="pyarrow", index=True, compression="snappy")

    manifest = CacheManifest(
        symbol=symbol,
        timeframe=tf,
        rows=len(combined),
        first_timestamp=combined.index[0].to_pydatetime() if len(combined) else None,
        last_timestamp=combined.index[-1].to_pydatetime() if len(combined) else None,
        digits=digits,
        broker_utc_offset_hours=s.broker_utc_offset_hours,
        content_hash=content_hash(combined),
        source=source,
        updated_at=datetime.now(timezone.utc),
    )
    s.manifest_path(symbol, tf).write_text(
        manifest.model_dump_json(indent=2), encoding="utf-8"
    )
    return manifest


def last_cached_timestamp(
    symbol: str, timeframe: str, settings: Settings | None = None
) -> pd.Timestamp | None:
    """Where an incremental fetch should resume from."""
    manifest = read_manifest(symbol, timeframe, settings)
    if manifest is None or manifest.last_timestamp is None:
        return None
    return pd.Timestamp(manifest.last_timestamp).tz_convert("UTC")


def list_cached(settings: Settings | None = None) -> list[CacheManifest]:
    s = settings or get_settings()
    out: list[CacheManifest] = []
    for manifest_file in sorted(Path(s.cache_dir).glob("*/*.manifest.json")):
        try:
            out.append(CacheManifest.model_validate_json(manifest_file.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, ValueError):
            continue
    return out
