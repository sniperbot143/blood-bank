"""SMC Trading Intelligence -- command line entry point.

Phase 1 commands (data layer only, read-only, no trading):

    python main.py ingest  --symbol XAUUSDm --tf M5 --bars 200000
    python main.py ingest  --csv data/raw/XAUUSD_M5.csv --symbol XAUUSDm --tf M5
    python main.py inspect --symbol XAUUSDm --tf M5
    python main.py symbols --search XAU
    python main.py status
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

# Make `python main.py` work from any working directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config.settings import TIMEFRAMES, Settings, get_settings, get_timeframe  # noqa: E402
from data import cache  # noqa: E402
from data.csv_loader import load_csv  # noqa: E402
from data.normalizer import NormalizationError, NormalizedBars, validate_frame  # noqa: E402

log = logging.getLogger("smc")


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(levelname)s %(message)s",
    )


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    ts = pd.Timestamp(value)
    return (ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")).to_pydatetime()


def _save_quarantine(result: NormalizedBars, settings: Settings, symbol: str, tf: str) -> Path | None:
    if result.quarantined.empty:
        return None
    out_dir = settings.data_dir / "quarantine"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"{symbol}_{tf}_{stamp}.csv"
    result.quarantined.to_csv(path)
    return path


# ---------------------------------------------------------------- commands

def cmd_ingest(args: argparse.Namespace, settings: Settings) -> int:
    tf = get_timeframe(args.tf).name
    symbol = args.symbol
    offset = args.offset if args.offset is not None else settings.broker_utc_offset_hours
    drop_forming = not args.include_forming

    if args.csv:
        result = load_csv(
            args.csv,
            symbol=symbol,
            timeframe=tf,
            broker_utc_offset_hours=offset,
            digits=args.digits,
            drop_forming=drop_forming,
        )
        source = f"csv:{Path(args.csv).name}"
        digits = args.digits
    else:
        from data.mt5_connector import MT5Connector, MT5Unavailable  # local import: optional dep

        try:
            with MT5Connector(settings) as mt:
                resolved = mt.resolve_symbol(symbol)
                if resolved != symbol:
                    log.info("resolved symbol %s -> %s", symbol, resolved)
                symbol = resolved
                spec = mt.symbol_spec(symbol)
                digits = spec.digits

                detected = mt.detect_server_utc_offset(symbol)
                if detected is not None:
                    log.info("detected broker UTC offset: %+.1f h (configured %+.1f h)",
                             detected, offset)
                    if abs(detected - offset) > 0.01:
                        log.warning(
                            "BROKER_UTC_OFFSET in .env is %+.1f but the terminal says "
                            "%+.1f -- session boundaries will be wrong until this matches",
                            offset, detected,
                        )

                start = _parse_dt(args.start)
                if start is None and not args.full:
                    last = cache.last_cached_timestamp(symbol, tf, settings)
                    if last is not None:
                        start = last.to_pydatetime()
                        log.info("incremental fetch from %s", start)

                result = mt.fetch_bars(
                    symbol, tf,
                    bars=None if start else args.bars,
                    start=start,
                    end=_parse_dt(args.end),
                    drop_forming=drop_forming,
                )
                source = "mt5"
        except MT5Unavailable as exc:
            log.error("%s", exc)
            return 2

    print(result.report.summary())

    if q_path := _save_quarantine(result, settings, symbol, tf):
        print(f"quarantined rows written to {q_path}")

    weekday_gaps = [g for g in result.report.gaps if g.kind == "other"]
    if weekday_gaps:
        print(f"\nnon-weekend gaps needing review ({len(weekday_gaps)}, showing up to 10):")
        for gap in weekday_gaps[:10]:
            print(f"  {gap.previous:%Y-%m-%d %H:%M} -> {gap.current:%Y-%m-%d %H:%M} "
                  f"({gap.missing_bars} bars missing)")

    if result.frame.empty:
        log.error("nothing to cache: 0 valid bars after normalization")
        return 1

    manifest = cache.write_bars(
        result.frame,
        symbol=symbol,
        timeframe=tf,
        settings=settings,
        digits=digits,
        source=source,
        merge=not args.no_merge,
    )
    path = settings.cache_path(symbol, tf)
    size_mb = path.stat().st_size / 1e6
    print(f"cached -> {path} ({size_mb:.1f} MB, {manifest.rows:,} bars total)")
    return 0


def cmd_inspect(args: argparse.Namespace, settings: Settings) -> int:
    tf = get_timeframe(args.tf).name
    try:
        frame = cache.read_bars(args.symbol, tf, settings)
    except FileNotFoundError as exc:
        log.error("%s", exc)
        return 1

    manifest = cache.read_manifest(args.symbol, tf, settings)

    try:
        validate_frame(frame)
        verdict = "PASS"
    except NormalizationError as exc:
        verdict = f"FAIL -- {exc}"

    tf_delta = get_timeframe(tf).delta
    deltas = frame.index.to_series().diff().dropna()
    gap_rows = int(frame["gap_before"].sum())

    print(f"symbol            : {args.symbol}")
    print(f"timeframe         : {tf}")
    print(f"rows              : {len(frame):,}")
    if len(frame):
        print(f"range (UTC)       : {frame.index[0]:%Y-%m-%d %H:%M} -> {frame.index[-1]:%Y-%m-%d %H:%M}")
    print(f"schema validation : {verdict}")
    print(f"duplicate index   : {int(frame.index.duplicated().sum())}")
    print(f"monotonic index   : {frame.index.is_monotonic_increasing}")
    print(f"closed bars       : {int(frame['is_closed'].sum()):,} of {len(frame):,}")
    print(f"gap_before rows   : {gap_rows:,}")
    if len(deltas):
        print(f"modal bar spacing : {deltas.mode().iloc[0]} (expected {tf_delta})")
    if manifest:
        print(f"cached source     : {manifest.source}")
        print(f"digits            : {manifest.digits}")
        print(f"broker UTC offset : {manifest.broker_utc_offset_hours:+.1f} h")
        print(f"content hash      : {manifest.content_hash[:16]}...")
        print(f"updated at        : {manifest.updated_at}")

    if len(frame) and args.rows:
        cols = ["open", "high", "low", "close", "tick_volume", "spread", "gap_before"]
        print(f"\nfirst {args.rows} bars:\n{frame[cols].head(args.rows)}")
        print(f"\nlast {args.rows} bars:\n{frame[cols].tail(args.rows)}")
    return 0 if verdict == "PASS" else 1


def cmd_symbols(args: argparse.Namespace, settings: Settings) -> int:
    from data.mt5_connector import MT5Connector, MT5Unavailable

    try:
        with MT5Connector(settings) as mt:
            names = mt.symbols(args.search)
    except MT5Unavailable as exc:
        log.error("%s", exc)
        return 2

    if not names:
        print("no symbols matched")
        return 1
    print(f"{len(names)} symbol(s):")
    for name in names:
        print(f"  {name}")
    return 0


def cmd_status(_args: argparse.Namespace, settings: Settings) -> int:
    from data.mt5_connector import MT5_AVAILABLE

    print("SMC Trading Intelligence -- Phase 1 (data layer)")
    print(f"python            : {sys.version.split()[0]}")
    print(f"pandas            : {pd.__version__}")
    print(f"MT5 package       : {'available' if MT5_AVAILABLE else 'NOT available (CSV/Parquet mode)'}")
    print(f"cache dir         : {settings.cache_dir}")
    print(f"broker UTC offset : {settings.broker_utc_offset_hours:+.1f} h")
    print(f"drop forming bar  : {settings.drop_forming_bar}")

    manifests = cache.list_cached(settings)
    if not manifests:
        print("\nnothing cached yet.")
        return 0
    print(f"\ncached datasets ({len(manifests)}):")
    for m in manifests:
        span = (
            f"{m.first_timestamp:%Y-%m-%d} -> {m.last_timestamp:%Y-%m-%d}"
            if m.first_timestamp and m.last_timestamp else "empty"
        )
        print(f"  {m.symbol:<12} {m.timeframe:<4} {m.rows:>9,} bars  {span}  [{m.source}]")
    return 0


# ------------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="main.py", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--log-level", default=None, choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    sub = parser.add_subparsers(dest="command", required=True)

    p_ing = sub.add_parser("ingest", help="fetch/load candles, normalize, cache")
    p_ing.add_argument("--symbol", required=True)
    p_ing.add_argument("--tf", default=None, choices=sorted(TIMEFRAMES))
    p_ing.add_argument("--bars", type=int, default=None, help="most recent N bars (MT5 mode)")
    p_ing.add_argument("--csv", default=None, help="load from CSV/TSV/Parquet instead of MT5")
    p_ing.add_argument("--start", default=None, help="ISO datetime, e.g. 2019-01-01")
    p_ing.add_argument("--end", default=None)
    p_ing.add_argument("--full", action="store_true", help="ignore cache, refetch from scratch")
    p_ing.add_argument("--offset", type=float, default=None, help="override broker UTC offset (hours)")
    p_ing.add_argument("--digits", type=int, default=None, help="price rounding (CSV mode)")
    p_ing.add_argument("--include-forming", action="store_true",
                       help="keep the unfinished bar (never use for signals)")
    p_ing.add_argument("--no-merge", action="store_true", help="overwrite the cache file")
    p_ing.set_defaults(func=cmd_ingest)

    p_ins = sub.add_parser("inspect", help="validate and describe a cached dataset")
    p_ins.add_argument("--symbol", required=True)
    p_ins.add_argument("--tf", default=None, choices=sorted(TIMEFRAMES))
    p_ins.add_argument("--rows", type=int, default=5, help="head/tail rows to print (0 = none)")
    p_ins.set_defaults(func=cmd_inspect)

    p_sym = sub.add_parser("symbols", help="list broker symbols (needs MT5)")
    p_sym.add_argument("--search", default=None)
    p_sym.set_defaults(func=cmd_symbols)

    sub.add_parser("status", help="show environment and cache state").set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    _setup_logging(args.log_level or settings.log_level)
    settings.ensure_dirs()

    if getattr(args, "tf", None) is None and hasattr(args, "tf"):
        args.tf = settings.default_timeframe
    if getattr(args, "bars", None) is None and getattr(args, "command", "") == "ingest":
        args.bars = settings.default_bars

    try:
        return args.func(args, settings)
    except (NormalizationError, FileNotFoundError, ValueError) as exc:
        log.error("%s", exc)
        return 1
    except BrokenPipeError:  # `... | head` closed the pipe; not an error
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
