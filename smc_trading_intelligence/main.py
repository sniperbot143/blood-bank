"""SMC Trading Intelligence -- command line entry point.

Read-only analysis. No order is ever placed by this program.

    python main.py ingest    --symbol XAUUSDm --tf M5 --bars 200000
    python main.py ingest    --csv data/raw/XAUUSD_M5.csv --symbol XAUUSDm --tf M5
    python main.py inspect   --symbol XAUUSDm --tf M5
    python main.py swings    --symbol XAUUSDm --tf M5
    python main.py structure --symbol XAUUSDm --tf M5
    python main.py breaks    --symbol XAUUSDm --tf M5
    python main.py liquidity --symbol XAUUSDm --tf M5
    python main.py sweeps    --symbol XAUUSDm --tf M5
    python main.py symbols   --search XAU
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


def cmd_swings(args: argparse.Namespace, settings: Settings) -> int:
    from config.smc_rules import SMCRules, SwingConfig, SwingMode
    from data.normalizer import closed_bars
    from structure.swings import detect_swings

    tf = get_timeframe(args.tf).name
    try:
        frame = closed_bars(cache.read_bars(args.symbol, tf, settings))
    except FileNotFoundError as exc:
        log.error("%s", exc)
        return 1

    if args.bars:
        frame = frame.iloc[-args.bars:]

    rules = SMCRules(
        atr_period=args.atr_period,
        swing=SwingConfig(
            mode=SwingMode(args.mode),
            swing_left=args.left,
            swing_right=args.right,
            min_swing_atr=args.min_atr,
        ),
    )
    series = detect_swings(frame, rules)

    at = len(frame) - 1
    if args.as_of is not None:
        at = int(args.as_of) if str(args.as_of).lstrip("-").isdigit() else int(
            frame.index.get_indexer([pd.Timestamp(args.as_of, tz="UTC")], method="ffill")[0]
        )
        if at < 0:
            log.error("--as-of %s is before the first bar", args.as_of)
            return 1

    live = series.as_of(at)
    print(f"symbol / timeframe : {series.symbol} {series.timeframe}")
    print(f"bars analysed      : {len(frame):,}")
    print(f"mode               : {rules.swing.mode.value} "
          f"(left={rules.swing.swing_left}, right={rules.swing.swing_right}, "
          f"min_swing_atr={rules.swing.min_swing_atr})")
    print(f"rules hash         : {rules.rules_hash[:16]}...")
    print(f"swings accepted    : {len(series.swings):,}")
    print(f"candidates rejected: {len(series.rejected):,} {series.reject_counts()}")
    print(f"state at bar       : {at} ({frame.index[at]:%Y-%m-%d %H:%M} UTC)")
    print(f"live chain length  : {len(live):,}")
    print(f"chain alternates   : {series.alternates(at)}")

    if live and args.last:
        print(f"\nlast {min(args.last, len(live))} live swings:")
        print(f"  {'kind':<5} {'formed (UTC)':<17} {'price':>10} {'conf. bar':>10} {'ATR str':>8}")
        for swing in live[-args.last:]:
            print(f"  {swing.kind.value:<5} {swing.formed_at:%Y-%m-%d %H:%M} "
                  f"{swing.price:>10.5f} {swing.confirmed_at_index:>10} "
                  f"{swing.strength_atr:>8.2f}")

    superseded = [s for s in series.swings if s.superseded_at_index is not None]
    print(f"\nsuperseded (kept for history, not erased): {len(superseded):,}")
    return 0


def _resolve_bar(frame: pd.DataFrame, as_of: str | None) -> int:
    """Bar index from an index number or a timestamp; last bar when unset."""
    if as_of is None:
        return len(frame) - 1
    if str(as_of).lstrip("-").isdigit():
        return int(as_of)
    return int(frame.index.get_indexer([pd.Timestamp(as_of, tz="UTC")], method="ffill")[0])


def cmd_structure(args: argparse.Namespace, settings: Settings) -> int:
    from config.smc_rules import SMCRules, StructureConfig, SwingConfig, SwingMode
    from data.normalizer import closed_bars
    from structure.market_structure import build_structure

    tf = get_timeframe(args.tf).name
    try:
        frame = closed_bars(cache.read_bars(args.symbol, tf, settings))
    except FileNotFoundError as exc:
        log.error("%s", exc)
        return 1

    if args.bars:
        frame = frame.iloc[-args.bars:]
    if frame.empty:
        log.error("no bars to analyse")
        return 1

    rules = SMCRules(
        atr_period=args.atr_period,
        swing=SwingConfig(mode=SwingMode(args.mode), swing_left=args.left,
                          swing_right=args.right, min_swing_atr=args.min_atr),
        structure=StructureConfig(equal_tolerance_atr=args.equal_atr,
                                  range_atr_mult=args.range_atr,
                                  track_internal=not args.no_internal),
    )
    ms = build_structure(frame, rules)

    at = _resolve_bar(frame, args.as_of)
    if at < 0:
        log.error("--as-of %s is before the first bar", args.as_of)
        return 1
    state = ms.state_at(at)

    print(f"symbol / timeframe : {ms.symbol} {ms.timeframe}")
    print(f"bars analysed      : {len(frame):,}")
    print(f"rules hash         : {rules.rules_hash[:16]}...")
    print(f"swings / labels    : {len(ms.swings.swings):,} / {len(ms.labels):,}")
    print(f"label counts       : {ms.label_counts()}")
    print(f"bias share         : "
          f"{ {k: f'{v:.1%}' for k, v in ms.bias_share().items()} }")
    print(f"bias changes       : {len(ms.changes):,}")
    print(f"\nstate at bar {at} ({frame.index[at]:%Y-%m-%d %H:%M} UTC)")
    print(state.describe())

    if ms.internal is not None:
        internal = ms.internal.state_at(at)
        print(f"internal bias    : {internal.bias.value} "
              f"({len(ms.internal.labels):,} internal labels)")

    known = ms.labels_known_at(at)
    if known and args.last:
        print(f"\nlast {min(args.last, len(known))} confirmed labels:")
        print(f"  {'label':<11} {'formed (UTC)':<17} {'price':>10} {'conf. bar':>10}")
        for item in known[-args.last:]:
            print(f"  {item.label.value:<11} {item.swing.formed_at:%Y-%m-%d %H:%M} "
                  f"{item.price:>10.5f} {item.confirmed_at_index:>10}")

    recent = [c for c in ms.changes if c.index <= at]
    if recent and args.last:
        print(f"\nlast {min(args.last, len(recent))} bias changes:")
        for change in recent[-args.last:]:
            print(f"  {change.timestamp:%Y-%m-%d %H:%M}  "
                  f"{change.previous.value:>7} -> {change.current.value:<7}  ({change.reason})")
    return 0


def cmd_breaks(args: argparse.Namespace, settings: Settings) -> int:
    from config.smc_rules import (
        BOSMode, BreakConfig, SMCRules, StructureConfig, SwingConfig, SwingMode,
    )
    from data.normalizer import closed_bars
    from structure.breaks import BreakType
    from structure.market_structure import build_structure

    tf = get_timeframe(args.tf).name
    try:
        frame = closed_bars(cache.read_bars(args.symbol, tf, settings))
    except FileNotFoundError as exc:
        log.error("%s", exc)
        return 1

    if args.bars:
        frame = frame.iloc[-args.bars:]
    if frame.empty:
        log.error("no bars to analyse")
        return 1

    rules = SMCRules(
        atr_period=args.atr_period,
        swing=SwingConfig(mode=SwingMode(args.mode), swing_left=args.left,
                          swing_right=args.right, min_swing_atr=args.min_atr),
        structure=StructureConfig(range_atr_mult=args.range_atr, track_internal=False),
        breaks=BreakConfig(bos_mode=BOSMode(args.bos_mode),
                           mss_min_displacement=args.min_displacement,
                           mss_confirm_window=args.mss_window),
    )
    structure = build_structure(frame, rules, with_breaks=True)
    breaks = structure.breaks

    at = _resolve_bar(frame, args.as_of)
    if at < 0:
        log.error("--as-of %s is before the first bar", args.as_of)
        return 1

    known = breaks.events_known_at(at)
    counts = breaks.counts()
    bias_bars: dict[str, int] = {}
    for t in range(len(frame)):
        key = breaks.bias_at(t).value
        bias_bars[key] = bias_bars.get(key, 0) + 1

    print(f"symbol / timeframe : {structure.symbol} {structure.timeframe}")
    print(f"bars analysed      : {len(frame):,}")
    print(f"rules hash         : {rules.rules_hash[:16]}...")
    print(f"bos mode           : {rules.breaks.bos_mode.value}")
    print(f"mss threshold      : {rules.breaks.mss_min_displacement} "
          f"(window {rules.breaks.mss_confirm_window} bars)")
    print(f"events             : {len(breaks.events):,} {counts}")
    print(f"expired CHOCH      : {breaks.expired_choch:,}")
    print(f"bias share (breaks): "
          f"{ {k: f'{v / len(frame):.1%}' for k, v in sorted(bias_bars.items())} }")

    state = structure.state_at(at)
    print(f"\nstate at bar {at} ({frame.index[at]:%Y-%m-%d %H:%M} UTC)")
    print(state.describe())

    if known and args.last:
        print(f"\nlast {min(args.last, len(known))} events:")
        print(f"  {'type':<6} {'dir':<8} {'when (UTC)':<17} {'level':>10} "
              f"{'close':>10} {'disp':>6}  bias")
        for event in known[-args.last:]:
            disp = ("  n/a" if event.displacement.score != event.displacement.score
                    else f"{event.displacement.score:.2f}")
            print(f"  {event.type.value:<6} {event.direction.value:<8} "
                  f"{event.timestamp:%Y-%m-%d %H:%M} {event.broken_level:>10.5f} "
                  f"{event.break_price:>10.5f} {disp:>6}  "
                  f"{event.bias_before.value}->{event.bias_after.value}")
    return 0


def cmd_liquidity(args: argparse.Namespace, settings: Settings) -> int:
    from config.smc_rules import LiquidityConfig, SMCRules, SwingConfig, SwingMode
    from data.normalizer import closed_bars
    from liquidity.levels import build_liquidity

    tf = get_timeframe(args.tf).name
    try:
        frame = closed_bars(cache.read_bars(args.symbol, tf, settings))
    except FileNotFoundError as exc:
        log.error("%s", exc)
        return 1

    if args.bars:
        frame = frame.iloc[-args.bars:]
    if frame.empty:
        log.error("no bars to analyse")
        return 1

    rules = SMCRules(
        atr_period=args.atr_period,
        swing=SwingConfig(mode=SwingMode(args.mode), swing_left=args.left,
                          swing_right=args.right, min_swing_atr=args.min_atr),
        liquidity=LiquidityConfig(
            equal_tolerance_atr=args.equal_atr,
            equal_max_gap_bars=args.equal_gap,
            day_start_hour=args.day_start,
            track_sessions=not args.no_sessions,
        ),
    )
    liquidity = build_liquidity(frame, rules)

    at = _resolve_bar(frame, args.as_of)
    if at < 0:
        log.error("--as-of %s is before the first bar", args.as_of)
        return 1
    price = float(frame["close"].iloc[at])

    print(f"symbol / timeframe : {liquidity.symbol} {liquidity.timeframe}")
    print(f"bars analysed      : {len(frame):,}")
    print(f"rules hash         : {rules.rules_hash[:16]}...")
    print(f"pools found        : {len(liquidity.pools):,}")
    print(f"known at bar {at}   : {len(liquidity.known_at(at)):,} "
          f"{liquidity.counts(at)}")
    print(f"status             : {liquidity.status_counts(at)}")
    print(f"sessions           : {liquidity.sessions.counts()}")

    session = liquidity.sessions.session_at(at)
    print(f"\nbar {at} ({frame.index[at]:%Y-%m-%d %H:%M} UTC) close {price:.5f}"
          f"  session: {session or '-'}")

    def show(title: str, pools: list) -> None:
        if not pools:
            return
        print(f"\n{title}")
        print(f"  {'kind':<13} {'price':>11} {'dist':>8} {'status':<9} "
              f"{'touch':>5} {'str':>5}  origin")
        for pool in pools[: args.last]:
            pool_price = pool.price_at(at)
            print(f"  {pool.kind.value:<13} {pool_price:>11.5f} "
                  f"{abs(pool_price - price):>8.5f} {pool.status_at(at).value:<9} "
                  f"{pool.touch_count_at(at):>5} {pool.strength_at(at):>5.2f}  {pool.origin}")

    show("buy-side liquidity above price (nearest first):",
         liquidity.above(price, at))
    show("sell-side liquidity below price (nearest first):",
         liquidity.below(price, at))
    return 0


def cmd_sweeps(args: argparse.Namespace, settings: Settings) -> int:
    from config.smc_rules import SMCRules, SweepConfig, SwingConfig
    from data.normalizer import closed_bars
    from liquidity.levels import build_liquidity
    from liquidity.sweeps import detect_sweeps
    from structure.market_structure import build_structure

    tf = get_timeframe(args.tf).name
    try:
        frame = closed_bars(cache.read_bars(args.symbol, tf, settings))
    except FileNotFoundError as exc:
        log.error("%s", exc)
        return 1
    if args.bars:
        frame = frame.iloc[-args.bars:]
    if frame.empty:
        log.error("no bars to analyse")
        return 1

    rules = SMCRules(
        swing=SwingConfig(swing_left=args.left, swing_right=args.right,
                          min_swing_atr=args.min_atr),
        sweeps=SweepConfig(min_penetration_atr=args.min_pen, max_penetration_atr=args.max_pen,
                           confirm_bars=args.confirm, max_close_location=args.max_close_loc),
    )
    liquidity = build_liquidity(frame, rules)
    structure = build_structure(frame, rules)
    sweeps = detect_sweeps(frame, liquidity, rules, structure=structure)

    print(f"symbol / timeframe : {sweeps.symbol} {sweeps.timeframe}")
    print(f"bars analysed      : {len(frame):,}")
    print(f"pools scanned      : {len(liquidity.pools):,}")
    print(f"sweeps             : {len(sweeps.events):,} {sweeps.type_counts()}")
    print(f"rejected breakouts : {sweeps.rejected_breakouts:,} "
          f"(penetration > {rules.sweeps.max_penetration_atr} x ATR)")
    print(f"sweep rate         : {len(sweeps.events) / max(1, len(liquidity.pools)):.1%} of pools")
    print(f"by pool kind       : {sweeps.counts()}")

    if sweeps.events and args.last:
        print(f"\nlast {min(args.last, len(sweeps.events))} sweeps:")
        print(f"  {'type':<16} {'pool':<13} {'when (UTC)':<17} {'level':>10} "
              f"{'mag':>5} {'rej':>5} {'bars':>4}  session")
        for event in sweeps.events[-args.last:]:
            print(f"  {event.type.value:<16} {event.pool_kind.value:<13} "
                  f"{event.confirmed_at:%Y-%m-%d %H:%M} {event.level:>10.5f} "
                  f"{event.magnitude_atr:>5.2f} {event.rejection_atr:>5.2f} "
                  f"{event.bars_to_reject:>4}  {event.session or '-'}")
    return 0


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

    p_sw = sub.add_parser("swings", help="detect swing points on cached bars")
    p_sw.add_argument("--symbol", required=True)
    p_sw.add_argument("--tf", default=None, choices=sorted(TIMEFRAMES))
    p_sw.add_argument("--bars", type=int, default=None, help="use only the last N bars")
    p_sw.add_argument("--mode", default="FRACTAL",
                      choices=["FRACTAL", "FIXED_LOOKBACK", "ATR_ADAPTIVE"])
    p_sw.add_argument("--left", type=int, default=3)
    p_sw.add_argument("--right", type=int, default=3)
    p_sw.add_argument("--min-atr", type=float, default=0.5, dest="min_atr")
    p_sw.add_argument("--atr-period", type=int, default=14, dest="atr_period")
    p_sw.add_argument("--as-of", default=None,
                      help="bar index or timestamp: show the chain as it was known then")
    p_sw.add_argument("--last", type=int, default=10)
    p_sw.set_defaults(func=cmd_swings)

    p_st = sub.add_parser("structure", help="label HH/HL/LH/LL and show the bias timeline")
    p_st.add_argument("--symbol", required=True)
    p_st.add_argument("--tf", default=None, choices=sorted(TIMEFRAMES))
    p_st.add_argument("--bars", type=int, default=None, help="use only the last N bars")
    p_st.add_argument("--mode", default="FRACTAL",
                      choices=["FRACTAL", "FIXED_LOOKBACK", "ATR_ADAPTIVE"])
    p_st.add_argument("--left", type=int, default=3)
    p_st.add_argument("--right", type=int, default=3)
    p_st.add_argument("--min-atr", type=float, default=0.5, dest="min_atr")
    p_st.add_argument("--atr-period", type=int, default=14, dest="atr_period")
    p_st.add_argument("--equal-atr", type=float, default=0.05, dest="equal_atr",
                      help="EQH/EQL tolerance in ATR")
    p_st.add_argument("--range-atr", type=float, default=2.0, dest="range_atr",
                      help="dealing range narrower than this many ATR is RANGE")
    p_st.add_argument("--no-internal", action="store_true")
    p_st.add_argument("--as-of", default=None, help="bar index or timestamp")
    p_st.add_argument("--last", type=int, default=8)
    p_st.set_defaults(func=cmd_structure)

    p_br = sub.add_parser("breaks", help="detect BOS / CHOCH / MSS")
    p_br.add_argument("--symbol", required=True)
    p_br.add_argument("--tf", default=None, choices=sorted(TIMEFRAMES))
    p_br.add_argument("--bars", type=int, default=None)
    p_br.add_argument("--mode", default="FRACTAL",
                      choices=["FRACTAL", "FIXED_LOOKBACK", "ATR_ADAPTIVE"])
    p_br.add_argument("--left", type=int, default=5)
    p_br.add_argument("--right", type=int, default=5)
    p_br.add_argument("--min-atr", type=float, default=2.0, dest="min_atr")
    p_br.add_argument("--atr-period", type=int, default=14, dest="atr_period")
    p_br.add_argument("--range-atr", type=float, default=2.0, dest="range_atr")
    p_br.add_argument("--bos-mode", default="CLOSE_ONLY", dest="bos_mode",
                      choices=["CLOSE_ONLY", "WICK_OR_CLOSE", "DISPLACEMENT_CONFIRMATION"])
    p_br.add_argument("--min-displacement", type=float, default=0.55, dest="min_displacement")
    p_br.add_argument("--mss-window", type=int, default=10, dest="mss_window")
    p_br.add_argument("--as-of", default=None, help="bar index or timestamp")
    p_br.add_argument("--last", type=int, default=10)
    p_br.set_defaults(func=cmd_breaks)

    p_lq = sub.add_parser("liquidity", help="map liquidity pools and their lifecycle")
    p_lq.add_argument("--symbol", required=True)
    p_lq.add_argument("--tf", default=None, choices=sorted(TIMEFRAMES))
    p_lq.add_argument("--bars", type=int, default=None)
    p_lq.add_argument("--mode", default="FRACTAL",
                      choices=["FRACTAL", "FIXED_LOOKBACK", "ATR_ADAPTIVE"])
    p_lq.add_argument("--left", type=int, default=5)
    p_lq.add_argument("--right", type=int, default=5)
    p_lq.add_argument("--min-atr", type=float, default=2.0, dest="min_atr")
    p_lq.add_argument("--atr-period", type=int, default=14, dest="atr_period")
    p_lq.add_argument("--equal-atr", type=float, default=0.10, dest="equal_atr",
                      help="EQH/EQL tolerance in ATR")
    p_lq.add_argument("--equal-gap", type=int, default=50, dest="equal_gap",
                      help="max bars between equal-level members")
    p_lq.add_argument("--day-start", type=int, default=0, dest="day_start",
                      help="hour (UTC) the broker day starts")
    p_lq.add_argument("--no-sessions", action="store_true")
    p_lq.add_argument("--as-of", default=None, help="bar index or timestamp")
    p_lq.add_argument("--last", type=int, default=6, help="pools to show per side")
    p_lq.set_defaults(func=cmd_liquidity)

    p_sw2 = sub.add_parser("sweeps", help="detect liquidity sweeps")
    p_sw2.add_argument("--symbol", required=True)
    p_sw2.add_argument("--tf", default=None, choices=sorted(TIMEFRAMES))
    p_sw2.add_argument("--bars", type=int, default=None)
    p_sw2.add_argument("--left", type=int, default=5)
    p_sw2.add_argument("--right", type=int, default=5)
    p_sw2.add_argument("--min-atr", type=float, default=2.0, dest="min_atr")
    p_sw2.add_argument("--min-pen", type=float, default=0.02, dest="min_pen")
    p_sw2.add_argument("--max-pen", type=float, default=1.5, dest="max_pen")
    p_sw2.add_argument("--confirm", type=int, default=2)
    p_sw2.add_argument("--max-close-loc", type=float, default=0.40, dest="max_close_loc")
    p_sw2.add_argument("--last", type=int, default=8)
    p_sw2.set_defaults(func=cmd_sweeps)

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
