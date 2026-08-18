"""MetaTrader 5 read-only data access.

Import-guarded on purpose: the `MetaTrader5` package is Windows-only, and the
rest of this project must run on any OS from CSV/Parquet. Nothing here is
imported at module scope by the SMC engine.

READ ONLY. No order functions are exposed from this module, by design -- see
docs/ARCHITECTURE.md section 9.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pandas as pd

from config.settings import Settings, get_settings, get_timeframe
from data.normalizer import NormalizedBars, normalize

try:  # pragma: no cover - platform dependent
    import MetaTrader5 as mt5  # type: ignore
except Exception:  # ImportError on non-Windows, OSError on a broken install
    mt5 = None  # type: ignore[assignment]

MT5_AVAILABLE = mt5 is not None

# Suffixes brokers bolt onto the standard symbol name (Exness "m" accounts,
# ICMarkets "raw"/".a", etc.). Order matters: most common first.
COMMON_SUFFIXES = ["", "m", ".m", "_m", "micro", "c", ".c", "z", "#", ".a", ".raw", "-ECN", ".ecn", "pro", ".pro", "i", ".i"]


class MT5Unavailable(RuntimeError):
    """MT5 cannot be used here -- with an explanation of what to do instead."""


@dataclass(frozen=True)
class SymbolSpec:
    """The broker facts every downstream module needs (digits, lot rules)."""

    name: str
    digits: int
    point: float
    spread: int
    trade_stops_level: int
    volume_min: float
    volume_max: float
    volume_step: float
    contract_size: float
    currency_profit: str

    @property
    def tick_size(self) -> float:
        return self.point


def suffix_candidates(base: str) -> list[str]:
    """Candidate broker names for a base symbol, e.g. XAUUSD -> XAUUSDm, ..."""
    stem = re.sub(r"[^A-Za-z0-9]+$", "", base.strip().upper())
    seen: dict[str, None] = {}
    for suffix in COMMON_SUFFIXES:
        for candidate in (f"{stem}{suffix}", f"{stem}{suffix.upper()}"):
            seen.setdefault(candidate, None)
    return list(seen)


def round_offset_hours(delta_seconds: float) -> float:
    """Round a raw server-vs-UTC delta to the nearest half hour."""
    return round(delta_seconds / 1800.0) / 2.0


class MT5Connector:
    """Thin, honest wrapper around the MT5 terminal.

    Usage::

        with MT5Connector() as mt:
            name = mt.resolve_symbol("XAUUSD")
            bars = mt.fetch_bars(name, "M5", bars=100_000)
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._connected = False

    # -- lifecycle ---------------------------------------------------------

    def __enter__(self) -> MT5Connector:
        self.connect()
        return self

    def __exit__(self, *_exc) -> None:
        self.shutdown()

    def _require(self) -> None:
        if not MT5_AVAILABLE:
            raise MT5Unavailable(
                "The MetaTrader5 python package is not importable here.\n"
                "  * It only runs on Windows (pip install MetaTrader5).\n"
                "  * On Linux/macOS, export bars from MT5 to CSV and use:\n"
                "      python main.py ingest --csv <file> --symbol <SYM> --tf M5"
            )
        if not self._connected:
            raise MT5Unavailable("not connected -- call connect() first")

    def connect(self) -> None:
        """Initialize the terminal. Credentials are optional (an already
        logged-in terminal is enough for read-only data)."""
        if not MT5_AVAILABLE:
            raise MT5Unavailable(
                "MetaTrader5 package unavailable (Windows-only). Use --csv ingestion."
            )
        if self._connected:
            return

        kwargs: dict = {}
        s = self.settings
        if s.mt5_path:
            kwargs["path"] = s.mt5_path
        if s.mt5_login and s.mt5_password and s.mt5_server:
            kwargs |= {"login": int(s.mt5_login), "password": s.mt5_password, "server": s.mt5_server}

        if not mt5.initialize(**kwargs):
            code, msg = mt5.last_error()
            raise MT5Unavailable(
                f"MT5 initialize() failed: ({code}) {msg}\n"
                "Checks: terminal open and logged in; Tools > Options > Expert "
                "Advisors > 'Allow algorithmic trading'; MT5_PATH in .env points "
                "at terminal64.exe; credentials in .env if the terminal is fresh."
            )
        self._connected = True

    def shutdown(self) -> None:
        if self._connected and MT5_AVAILABLE:
            mt5.shutdown()
        self._connected = False

    # -- symbols -----------------------------------------------------------

    def symbols(self, search: str | None = None) -> list[str]:
        """All broker symbols, optionally filtered by a case-insensitive substring."""
        self._require()
        infos = mt5.symbols_get() or ()
        names = [i.name for i in infos]
        if search:
            needle = search.upper()
            names = [n for n in names if needle in n.upper()]
        return sorted(names)

    def resolve_symbol(self, base: str) -> str:
        """Map a plain symbol to this broker's actual name.

        Never guesses silently: on failure it raises with the candidates the
        broker really offers, so the user can pick.
        """
        self._require()
        available = {n.upper(): n for n in self.symbols()}

        if base.upper() in available:
            return available[base.upper()]
        for candidate in suffix_candidates(base):
            if candidate.upper() in available:
                return available[candidate.upper()]

        stem = re.sub(r"[^A-Za-z0-9]", "", base.upper())[:3]
        close = [n for n in available.values() if stem and stem in n.upper()][:20]
        raise MT5Unavailable(
            f"symbol {base!r} not found on this broker.\n"
            + (f"Similar available symbols: {close}" if close else
               "Nothing similar found -- check Market Watch > Show All.")
        )

    def symbol_spec(self, symbol: str) -> SymbolSpec:
        self._require()
        info = mt5.symbol_info(symbol)
        if info is None:
            if not mt5.symbol_select(symbol, True):
                raise MT5Unavailable(
                    f"symbol {symbol!r} is not selectable; enable it in Market Watch"
                )
            info = mt5.symbol_info(symbol)
        return SymbolSpec(
            name=info.name,
            digits=int(info.digits),
            point=float(info.point),
            spread=int(info.spread),
            trade_stops_level=int(info.trade_stops_level),
            volume_min=float(info.volume_min),
            volume_max=float(info.volume_max),
            volume_step=float(info.volume_step),
            contract_size=float(info.trade_contract_size),
            currency_profit=str(info.currency_profit),
        )

    def detect_server_utc_offset(self, symbol: str) -> float | None:
        """Hours the broker's clock is ahead of UTC, from a live tick.

        Returns None outside market hours (a stale tick would give a wrong
        answer, and a wrong answer here silently shifts every session boundary).
        """
        self._require()
        tick = mt5.symbol_info_tick(symbol)
        if tick is None or not tick.time:
            return None
        now = datetime.now(timezone.utc)
        server = datetime.fromtimestamp(int(tick.time), tz=timezone.utc)
        if abs((now - server).total_seconds()) > 900:  # stale tick / market closed
            return None
        return round_offset_hours((server - now).total_seconds())

    # -- bars --------------------------------------------------------------

    def _mt5_timeframe(self, timeframe: str):
        return getattr(mt5, get_timeframe(timeframe).mt5_attr)

    def _rates_to_frame(self, rates) -> pd.DataFrame:
        if rates is None or len(rates) == 0:
            return pd.DataFrame()
        return pd.DataFrame(rates)

    def fetch_bars(
        self,
        symbol: str,
        timeframe: str,
        *,
        bars: int | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        drop_forming: bool | None = None,
    ) -> NormalizedBars:
        """Fetch candles and return them normalized.

        Either `bars` (most recent N) or a `start`/`end` range. Range requests
        are chunked so the terminal's per-call limit cannot silently truncate
        history -- the classic "I only got 5,000 bars" trap.
        """
        self._require()
        tf = get_timeframe(timeframe)
        mtf = self._mt5_timeframe(timeframe)
        spec = self.symbol_spec(symbol)

        if bars is not None and start is None:
            frames = []
            remaining, pos = int(bars), 0
            while remaining > 0:
                take = min(remaining, self.settings.max_bars_per_request)
                chunk = self._rates_to_frame(mt5.copy_rates_from_pos(symbol, mtf, pos, take))
                if chunk.empty:
                    break
                frames.append(chunk)
                got = len(chunk)
                pos += got
                remaining -= got
                if got < take:
                    break  # terminal has no more history cached
            raw = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        else:
            if start is None:
                raise ValueError("provide either bars=... or start=...")
            end = end or datetime.now(timezone.utc)
            frames, cursor = [], start
            span = tf.delta * self.settings.max_bars_per_request
            while cursor < end:
                stop = min(cursor + span, end)
                chunk = self._rates_to_frame(mt5.copy_rates_range(symbol, mtf, cursor, stop))
                if not chunk.empty:
                    frames.append(chunk)
                cursor = stop + timedelta(seconds=1)
            raw = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

        if raw.empty:
            code, msg = mt5.last_error()
            raise MT5Unavailable(
                f"no bars returned for {symbol} {tf.name}: ({code}) {msg}\n"
                "Open that chart in the terminal and scroll back to force a "
                "history download, and raise Tools > Options > Charts > "
                "'Max bars in chart'."
            )

        return normalize(
            raw,
            symbol=symbol,
            timeframe=tf.name,
            broker_utc_offset_hours=self.settings.broker_utc_offset_hours,
            digits=spec.digits,
            drop_forming=(
                self.settings.drop_forming_bar if drop_forming is None else drop_forming
            ),
        )

    def account_summary(self) -> dict:
        """Read-only account snapshot (used later for position sizing)."""
        self._require()
        info = mt5.account_info()
        if info is None:
            return {}
        return {
            "login": info.login,
            "server": info.server,
            "currency": info.currency,
            "balance": info.balance,
            "equity": info.equity,
            "leverage": info.leverage,
            "trade_allowed": bool(info.trade_allowed),
        }
