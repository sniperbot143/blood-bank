"""End-to-end CLI smoke tests: ingest a CSV, inspect the cache, show status."""

from __future__ import annotations

import pandas as pd
import pytest

import main as cli
from config.settings import get_settings
from data import cache
from tests.conftest import make_raw_bars


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Point the CLI's cached settings at an isolated data directory."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("BROKER_UTC_OFFSET", "0")
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def _write_csv(tmp_path, n=50, start="2024-01-02 09:00") -> str:
    path = tmp_path / "XAUUSDm_M5.csv"
    make_raw_bars(n, start=start).to_csv(path, index=False)
    return str(path)


def test_ingest_csv_then_inspect(env, capsys):
    csv_path = _write_csv(env)

    assert cli.main(["ingest", "--symbol", "XAUUSDm", "--tf", "M5", "--csv", csv_path]) == 0
    out = capsys.readouterr().out
    assert "XAUUSDm M5 | 50 bars" in out
    assert "cached ->" in out

    assert cli.main(["inspect", "--symbol", "XAUUSDm", "--tf", "M5"]) == 0
    out = capsys.readouterr().out
    assert "schema validation : PASS" in out
    assert "duplicate index   : 0" in out
    assert "monotonic index   : True" in out


def test_reingest_is_incremental_and_leaves_rows_identical(env):
    csv_path = _write_csv(env, n=50)
    cli.main(["ingest", "--symbol", "XAUUSDm", "--tf", "M5", "--csv", csv_path])

    settings = get_settings()
    first = cache.read_bars("XAUUSDm", "M5", settings)
    hash_before = cache.read_manifest("XAUUSDm", "M5", settings).content_hash

    cli.main(["ingest", "--symbol", "XAUUSDm", "--tf", "M5", "--csv", csv_path])
    second = cache.read_bars("XAUUSDm", "M5", settings)

    pd.testing.assert_frame_equal(first, second)
    assert cache.read_manifest("XAUUSDm", "M5", settings).content_hash == hash_before


def test_ingest_reports_bad_rows_and_quarantines_them(env, capsys):
    raw = make_raw_bars(20)
    raw.loc[7, "high"] = 1.0          # high below low
    path = env / "dirty.csv"
    raw.to_csv(path, index=False)

    assert cli.main(["ingest", "--symbol", "XAUUSDm", "--tf", "M5", "--csv", str(path)]) == 0
    out = capsys.readouterr().out
    assert "bad OHLC quarantined: 1" in out
    assert "quarantined rows written to" in out
    assert len(cache.read_bars("XAUUSDm", "M5", get_settings())) == 19


def test_ingest_lists_non_weekend_gaps_for_review(env, capsys):
    raw = pd.concat(
        [make_raw_bars(10, start="2024-01-02 09:00"),
         make_raw_bars(10, start="2024-01-02 15:00")],
        ignore_index=True,
    )
    path = env / "gappy.csv"
    raw.to_csv(path, index=False)

    cli.main(["ingest", "--symbol", "XAUUSDm", "--tf", "M5", "--csv", str(path)])
    out = capsys.readouterr().out
    assert "non-weekend gaps needing review (1" in out


def test_status_runs_without_mt5(env, capsys):
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "Phase 1 (data layer)" in out
    assert "MT5 package" in out


def test_status_lists_cached_datasets(env, capsys):
    cli.main(["ingest", "--symbol", "XAUUSDm", "--tf", "M5", "--csv", _write_csv(env)])
    capsys.readouterr()

    cli.main(["status"])
    assert "XAUUSDm" in capsys.readouterr().out


def test_inspect_without_cache_fails_cleanly(env, caplog):
    assert cli.main(["inspect", "--symbol", "NOPEm", "--tf", "M5"]) == 1
    assert "no cache for NOPEm" in caplog.text


def test_missing_csv_file_returns_error_code(env, caplog):
    assert cli.main(["ingest", "--symbol", "X", "--tf", "M5", "--csv", "/nope/missing.csv"]) == 1
    assert "no such data file" in caplog.text


def test_ingest_via_mt5_without_the_package_returns_code_2(env, caplog):
    from data.mt5_connector import MT5_AVAILABLE

    if MT5_AVAILABLE:
        pytest.skip("MT5 present; the unavailable path can't be exercised")
    assert cli.main(["ingest", "--symbol", "XAUUSDm", "--tf", "M5", "--bars", "100"]) == 2
    assert "MetaTrader5" in caplog.text
