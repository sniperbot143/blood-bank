"""Phase 1 settings.

Everything configurable lives here or in a `.env` file. Nothing is hard-coded
in the data layer, and no credential is ever stored in source.

Load with::

    from config.settings import get_settings
    s = get_settings()
"""

from __future__ import annotations

import os
from datetime import timedelta
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

try:  # python-dotenv is a hard requirement, but never fatal at import time
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - only hit on a broken install
    def load_dotenv(*_args, **_kwargs):  # type: ignore[misc]
        return False


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_VERSION = 1


# --------------------------------------------------------------------------
# Timeframe registry
# --------------------------------------------------------------------------

class Timeframe(BaseModel):
    """A supported timeframe.

    `mt5_attr` is the attribute name on the MetaTrader5 module, resolved lazily
    so this file never imports MT5 (which is Windows-only).
    """

    name: str
    minutes: int
    mt5_attr: str

    @property
    def delta(self) -> timedelta:
        return timedelta(minutes=self.minutes)


TIMEFRAMES: dict[str, Timeframe] = {
    tf.name: tf
    for tf in [
        Timeframe(name="M1", minutes=1, mt5_attr="TIMEFRAME_M1"),
        Timeframe(name="M5", minutes=5, mt5_attr="TIMEFRAME_M5"),
        Timeframe(name="M15", minutes=15, mt5_attr="TIMEFRAME_M15"),
        Timeframe(name="M30", minutes=30, mt5_attr="TIMEFRAME_M30"),
        Timeframe(name="H1", minutes=60, mt5_attr="TIMEFRAME_H1"),
        Timeframe(name="H4", minutes=240, mt5_attr="TIMEFRAME_H4"),
        Timeframe(name="D1", minutes=1440, mt5_attr="TIMEFRAME_D1"),
    ]
}


def get_timeframe(name: str) -> Timeframe:
    """Look up a timeframe, failing loudly with the supported list."""
    key = name.strip().upper()
    if key not in TIMEFRAMES:
        raise ValueError(
            f"Unknown timeframe {name!r}. Supported: {', '.join(TIMEFRAMES)}"
        )
    return TIMEFRAMES[key]


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------

class Settings(BaseModel):
    """Validated runtime configuration.

    Optional credentials must never prevent the application from starting, so
    every MT5 field is nullable and the CSV/Parquet path works without them.
    """

    model_config = {"frozen": True}

    # paths
    project_root: Path = PROJECT_ROOT
    data_dir: Path = PROJECT_ROOT / "data"
    raw_dir: Path = PROJECT_ROOT / "data" / "raw"
    cache_dir: Path = PROJECT_ROOT / "data" / "cache"
    db_path: Path = PROJECT_ROOT / "database" / "smc.db"

    # MT5 (all optional)
    mt5_login: int | None = None
    mt5_password: str | None = None
    mt5_server: str | None = None
    mt5_path: str | None = None

    # time handling
    broker_utc_offset_hours: float = Field(
        default=0.0,
        ge=-14,
        le=14,
        description=(
            "Hours the broker's server clock is AHEAD of UTC. Naive timestamps "
            "from MT5/CSV are shifted by -offset to become UTC."
        ),
    )

    # defaults
    default_symbols: list[str] = Field(default_factory=lambda: ["XAUUSDm", "EURUSDm"])
    default_timeframe: str = "M5"
    default_bars: int = 200_000

    # ingestion behaviour
    drop_forming_bar: bool = True
    max_bars_per_request: int = 50_000
    log_level: str = "INFO"

    @field_validator("default_timeframe")
    @classmethod
    def _validate_tf(cls, v: str) -> str:
        return get_timeframe(v).name

    @field_validator("log_level")
    @classmethod
    def _validate_log(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR"}
        up = v.upper()
        if up not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}")
        return up

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.raw_dir, self.cache_dir, self.db_path.parent):
            d.mkdir(parents=True, exist_ok=True)

    def cache_path(self, symbol: str, timeframe: str) -> Path:
        return self.cache_dir / symbol / f"{get_timeframe(timeframe).name}.parquet"

    def manifest_path(self, symbol: str, timeframe: str) -> Path:
        return self.cache_dir / symbol / f"{get_timeframe(timeframe).name}.manifest.json"


def _env_str(key: str) -> str | None:
    raw = os.getenv(key)
    if raw is None:
        return None
    raw = raw.strip()
    return raw or None


def _env_float(key: str, default: float) -> float:
    raw = _env_str(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be a number, got {raw!r}") from exc


def _env_int(key: str) -> int | None:
    raw = _env_str(key)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer, got {raw!r}") from exc


def _env_bool(key: str, default: bool) -> bool:
    raw = _env_str(key)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def load_settings(env_file: str | Path | None = None) -> Settings:
    """Build Settings from environment + optional .env file."""
    load_dotenv(env_file or (PROJECT_ROOT / ".env"), override=False)

    symbols_raw = _env_str("DEFAULT_SYMBOLS")
    kwargs: dict = {
        "mt5_login": _env_int("MT5_LOGIN"),
        "mt5_password": _env_str("MT5_PASSWORD"),
        "mt5_server": _env_str("MT5_SERVER"),
        "mt5_path": _env_str("MT5_PATH"),
        "broker_utc_offset_hours": _env_float("BROKER_UTC_OFFSET", 0.0),
        "drop_forming_bar": _env_bool("DROP_FORMING_BAR", True),
        "log_level": _env_str("LOG_LEVEL") or "INFO",
    }
    if symbols_raw:
        kwargs["default_symbols"] = [s.strip() for s in symbols_raw.split(",") if s.strip()]
    if tf := _env_str("DEFAULT_TIMEFRAME"):
        kwargs["default_timeframe"] = tf
    if data_dir := _env_str("DATA_DIR"):
        base = Path(data_dir)
        kwargs |= {"data_dir": base, "raw_dir": base / "raw", "cache_dir": base / "cache"}

    return Settings(**kwargs)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings singleton."""
    return load_settings()
