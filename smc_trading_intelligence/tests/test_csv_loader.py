"""CSV / TSV / Parquet loading, including the MT5 export format."""

from __future__ import annotations

import pandas as pd
import pytest

from data.csv_loader import load_csv, read_raw
from data.normalizer import NormalizationError, validate_frame
from tests.conftest import NOW, make_raw_bars


def test_generic_csv_round_trip(tmp_path):
    path = tmp_path / "bars.csv"
    make_raw_bars(20).to_csv(path, index=False)

    result = load_csv(path, symbol="XAUUSDm", timeframe="M5", now=NOW)
    assert len(result.frame) == 20
    validate_frame(result.frame)


def test_mt5_export_tab_format_with_split_date_and_time(tmp_path):
    raw = make_raw_bars(6)
    export = pd.DataFrame(
        {
            "<DATE>": raw["time"].dt.strftime("%Y.%m.%d"),
            "<TIME>": raw["time"].dt.strftime("%H:%M:%S"),
            "<OPEN>": raw["open"],
            "<HIGH>": raw["high"],
            "<LOW>": raw["low"],
            "<CLOSE>": raw["close"],
            "<TICKVOL>": raw["tick_volume"],
            "<VOL>": 0,
            "<SPREAD>": raw["spread"],
        }
    )
    path = tmp_path / "XAUUSDm_M5.csv"
    export.to_csv(path, sep="\t", index=False)

    result = load_csv(path, symbol="XAUUSDm", timeframe="M5", now=NOW)
    assert len(result.frame) == 6
    assert result.frame.index[0] == pd.Timestamp("2024-01-02 09:00", tz="UTC")
    validate_frame(result.frame)


def test_semicolon_separator_is_sniffed(tmp_path):
    path = tmp_path / "bars_semi.csv"
    make_raw_bars(8).to_csv(path, index=False, sep=";")
    assert len(load_csv(path, symbol="X", timeframe="M5", now=NOW).frame) == 8


def test_parquet_input(tmp_path):
    path = tmp_path / "bars.parquet"
    make_raw_bars(12).to_parquet(path, index=False)
    assert len(load_csv(path, symbol="X", timeframe="M5", now=NOW).frame) == 12


def test_explicit_column_map(tmp_path):
    raw = make_raw_bars(5).rename(
        columns={"time": "when", "open": "o1", "high": "h1", "low": "l1", "close": "c1"}
    )
    path = tmp_path / "odd.csv"
    raw.to_csv(path, index=False)

    result = load_csv(
        path, symbol="X", timeframe="M5", now=NOW,
        column_map={"when": "timestamp", "o1": "open", "h1": "high", "l1": "low", "c1": "close"},
    )
    assert len(result.frame) == 5


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_raw(tmp_path / "nope.csv")


def test_unsupported_extension_raises(tmp_path):
    path = tmp_path / "bars.xlsx"
    path.write_bytes(b"not really a spreadsheet")
    with pytest.raises(NormalizationError, match="unsupported file type"):
        read_raw(path)


def test_empty_file_raises(tmp_path):
    path = tmp_path / "empty.csv"
    path.write_text("")
    with pytest.raises(NormalizationError, match="empty"):
        read_raw(path)
