"""Settings validation and the timeframe registry."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from config.settings import Settings, get_timeframe, load_settings


def test_timeframe_lookup_is_case_insensitive():
    assert get_timeframe("m5").name == "M5"
    assert get_timeframe(" H1 ").minutes == 60
    assert get_timeframe("D1").delta == timedelta(days=1)


def test_unknown_timeframe_lists_supported_values():
    with pytest.raises(ValueError, match="Supported: M1, M5"):
        get_timeframe("M7")


def test_defaults_require_no_credentials():
    s = Settings()
    assert s.mt5_login is None and s.mt5_password is None
    assert s.broker_utc_offset_hours == 0.0
    assert s.drop_forming_bar is True


def test_absurd_broker_offset_is_rejected():
    with pytest.raises(ValidationError):
        Settings(broker_utc_offset_hours=25)


def test_bad_log_level_is_rejected():
    with pytest.raises(ValidationError):
        Settings(log_level="CHATTY")


def test_settings_are_immutable():
    s = Settings()
    with pytest.raises(ValidationError):
        s.broker_utc_offset_hours = 3.0


def test_cache_paths_are_symbol_and_timeframe_scoped(tmp_path):
    s = Settings(cache_dir=tmp_path)
    assert s.cache_path("XAUUSDm", "m5").name == "M5.parquet"
    assert s.cache_path("XAUUSDm", "M5").parent.name == "XAUUSDm"
    assert s.manifest_path("XAUUSDm", "M5").name == "M5.manifest.json"


def test_env_overrides_are_read(monkeypatch, tmp_path):
    monkeypatch.setenv("BROKER_UTC_OFFSET", "2.5")
    monkeypatch.setenv("DEFAULT_SYMBOLS", "XAUUSDm, BTCUSDm")
    monkeypatch.setenv("DEFAULT_TIMEFRAME", "m15")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DROP_FORMING_BAR", "false")

    s = load_settings(env_file=tmp_path / "nonexistent.env")
    assert s.broker_utc_offset_hours == 2.5
    assert s.default_symbols == ["XAUUSDm", "BTCUSDm"]
    assert s.default_timeframe == "M15"
    assert s.cache_dir == tmp_path / "cache"
    assert s.drop_forming_bar is False


def test_non_numeric_offset_env_fails_loudly(monkeypatch, tmp_path):
    monkeypatch.setenv("BROKER_UTC_OFFSET", "two hours")
    with pytest.raises(ValueError, match="must be a number"):
        load_settings(env_file=tmp_path / "nonexistent.env")


def test_the_census_follows_data_dir(monkeypatch, tmp_path):
    """A relocated dataset must not silently query the default database."""
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    s = load_settings(env_file=tmp_path / "nonexistent.env")
    assert s.db_path == tmp_path / "smc.db"


def test_db_path_can_be_set_on_its_own(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "elsewhere" / "census.db"))

    s = load_settings(env_file=tmp_path / "nonexistent.env")
    assert s.db_path == tmp_path / "elsewhere" / "census.db"
