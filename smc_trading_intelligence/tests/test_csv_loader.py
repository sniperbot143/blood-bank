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


# ----------------------------------------------- headerless files & layouts

HISTDATA_SAMPLE = """2026.07.01,00:00,3980.635,3980.855,3979.335,3979.335,0
2026.07.01,00:01,3979.285,3979.655,3977.445,3977.645,0
2026.07.01,00:02,3977.835,3978.095,3975.355,3975.485,0
"""


def test_a_headerless_file_is_refused_until_its_columns_are_named(tmp_path):
    from data.csv_loader import read_raw

    path = tmp_path / "DAT_MT_XAUUSD_M1_202607.csv"
    path.write_text(HISTDATA_SAMPLE)

    with pytest.raises(NormalizationError, match="no header row"):
        read_raw(path)


def test_the_histdata_layout_reads_a_headerless_export(tmp_path):
    from data.csv_loader import read_raw

    path = tmp_path / "hist.csv"
    path.write_text(HISTDATA_SAMPLE)

    raw = read_raw(path, layout="histdata_mt")
    assert len(raw) == 3
    assert raw["timestamp"].iloc[0] == "2026.07.01 00:00"
    assert raw["high"].iloc[0] == pytest.approx(3980.855)


def test_a_layout_on_a_file_that_has_a_header_is_an_error(tmp_path):
    from data.csv_loader import read_raw

    path = tmp_path / "headed.csv"
    path.write_text("timestamp,open,high,low,close\n2024-01-01 00:00,1,2,0.5,1.5\n")

    with pytest.raises(NormalizationError, match="looks like it HAS a header"):
        read_raw(path, layout="ohlc")


def test_an_unknown_layout_names_the_ones_that_exist(tmp_path):
    from data.csv_loader import read_raw

    path = tmp_path / "hist.csv"
    path.write_text(HISTDATA_SAMPLE)

    with pytest.raises(NormalizationError, match="unknown layout"):
        read_raw(path, layout="metatrader9000")


def test_histdata_timestamps_convert_from_new_york_to_utc(tmp_path):
    """HistData MT files are New York time; July is EDT, so UTC is +4."""
    path = tmp_path / "hist.csv"
    path.write_text(HISTDATA_SAMPLE)

    result = load_csv(path, symbol="XAUUSD", timeframe="M1",
                      broker_utc_offset_hours=-4, layout="histdata_mt")
    assert result.frame.index[0] == pd.Timestamp("2026-07-01 04:00", tz="UTC")
