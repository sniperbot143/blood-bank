"""Cache round-trip, incremental merge and idempotency."""

from __future__ import annotations

import pandas as pd
import pytest

from data import cache
from data.normalizer import normalize, validate_frame
from tests.conftest import NOW, make_raw_bars


def _frame(n: int = 10, start: str = "2024-01-02 09:00") -> pd.DataFrame:
    return normalize(make_raw_bars(n, start=start), symbol="XAUUSDm",
                     timeframe="M5", now=NOW).frame


def test_write_then_read_round_trip(tmp_settings):
    frame = _frame(10)
    manifest = cache.write_bars(frame, symbol="XAUUSDm", timeframe="M5",
                                settings=tmp_settings, digits=2, source="test")

    assert manifest.rows == 10
    assert tmp_settings.cache_path("XAUUSDm", "M5").exists()

    back = cache.read_bars("XAUUSDm", "M5", tmp_settings)
    validate_frame(back)
    pd.testing.assert_frame_equal(back, frame)


def test_incremental_append_keeps_existing_rows_identical(tmp_settings):
    first = _frame(10, start="2024-01-02 09:00")     # 09:00 .. 09:45
    cache.write_bars(first, symbol="XAUUSDm", timeframe="M5", settings=tmp_settings)

    second = _frame(10, start="2024-01-02 09:50")    # continues the series
    cache.write_bars(second, symbol="XAUUSDm", timeframe="M5", settings=tmp_settings)

    merged = cache.read_bars("XAUUSDm", "M5", tmp_settings)
    assert len(merged) == 20
    pd.testing.assert_frame_equal(merged.iloc[:10], first)
    validate_frame(merged)


def test_gap_at_the_merge_boundary_is_detected(tmp_settings):
    cache.write_bars(_frame(10, start="2024-01-02 09:00"), symbol="XAUUSDm",
                     timeframe="M5", settings=tmp_settings)
    cache.write_bars(_frame(10, start="2024-01-02 14:00"), symbol="XAUUSDm",
                     timeframe="M5", settings=tmp_settings)

    merged = cache.read_bars("XAUUSDm", "M5", tmp_settings)
    flagged = merged.index[merged["gap_before"]]
    assert list(flagged) == [pd.Timestamp("2024-01-02 14:00", tz="UTC")]


def test_rewriting_the_same_data_is_idempotent(tmp_settings):
    frame = _frame(10)
    m1 = cache.write_bars(frame, symbol="XAUUSDm", timeframe="M5", settings=tmp_settings)
    m2 = cache.write_bars(frame, symbol="XAUUSDm", timeframe="M5", settings=tmp_settings)

    assert m1.rows == m2.rows == 10
    assert m1.content_hash == m2.content_hash


def test_overlapping_rewrite_prefers_the_new_revision(tmp_settings):
    original = _frame(10)
    cache.write_bars(original, symbol="XAUUSDm", timeframe="M5", settings=tmp_settings)

    revised = original.copy()
    revised.iloc[5, revised.columns.get_loc("tick_volume")] = 987

    cache.write_bars(revised, symbol="XAUUSDm", timeframe="M5", settings=tmp_settings)
    merged = cache.read_bars("XAUUSDm", "M5", tmp_settings)

    assert len(merged) == 10
    assert merged.iloc[5]["tick_volume"] == 987


def test_no_merge_overwrites(tmp_settings):
    cache.write_bars(_frame(10, "2024-01-02 09:00"), symbol="XAUUSDm",
                     timeframe="M5", settings=tmp_settings)
    cache.write_bars(_frame(3, "2024-01-03 09:00"), symbol="XAUUSDm",
                     timeframe="M5", settings=tmp_settings, merge=False)

    assert len(cache.read_bars("XAUUSDm", "M5", tmp_settings)) == 3


def test_manifest_records_provenance(tmp_settings):
    cache.write_bars(_frame(10), symbol="XAUUSDm", timeframe="M5",
                     settings=tmp_settings, digits=2, source="csv:demo.csv")

    manifest = cache.read_manifest("XAUUSDm", "M5", tmp_settings)
    assert manifest is not None
    assert manifest.digits == 2
    assert manifest.source == "csv:demo.csv"
    assert manifest.first_timestamp is not None
    assert len(manifest.content_hash) == 64
    assert manifest.schema_version == 1


def test_content_hash_changes_with_content(tmp_settings):
    frame = _frame(10)
    changed = frame.copy()
    changed.iloc[3, changed.columns.get_loc("close")] += 1.0
    assert cache.content_hash(frame) != cache.content_hash(changed)


def test_last_cached_timestamp_drives_incremental_fetch(tmp_settings):
    assert cache.last_cached_timestamp("XAUUSDm", "M5", tmp_settings) is None

    frame = _frame(10)
    cache.write_bars(frame, symbol="XAUUSDm", timeframe="M5", settings=tmp_settings)
    assert cache.last_cached_timestamp("XAUUSDm", "M5", tmp_settings) == frame.index[-1]


def test_reading_an_uncached_dataset_explains_the_fix(tmp_settings):
    with pytest.raises(FileNotFoundError, match="main.py ingest"):
        cache.read_bars("NOPEm", "M5", tmp_settings)


def test_list_cached(tmp_settings):
    cache.write_bars(_frame(5), symbol="XAUUSDm", timeframe="M5", settings=tmp_settings)
    cache.write_bars(_frame(5), symbol="EURUSDm", timeframe="M5", settings=tmp_settings)

    listed = {m.symbol for m in cache.list_cached(tmp_settings)}
    assert listed == {"XAUUSDm", "EURUSDm"}


def test_date_filtered_read(tmp_settings):
    frame = _frame(20)
    cache.write_bars(frame, symbol="XAUUSDm", timeframe="M5", settings=tmp_settings)

    subset = cache.read_bars("XAUUSDm", "M5", tmp_settings, start=frame.index[5])
    assert len(subset) == 15
