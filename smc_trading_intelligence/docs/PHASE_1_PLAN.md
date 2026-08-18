# Phase 1 — Data Ingestion & Normalization (proposed, awaiting approval)

**Nothing in this file has been built yet.** It is the scope proposal for the first
implementation phase.

---

## 1. What we are building

A data layer that turns "whatever the broker gives us" into one clean, trustworthy,
reusable candle table, plus a local cache so we never re-download the same history.

## 2. Why first

Every later module (swings, structure, probability, backtests) reads this table. A
silent flaw here — a duplicated bar, a timezone shift, a half-formed last candle —
corrupts every statistic downstream and is nearly invisible once buried. Cheap to
get right now, expensive later.

## 3. Deliverables

| file | purpose |
|---|---|
| `config/settings.py` | paths, symbols, timeframes, timezone, broker offset, Pydantic-validated |
| `data/mt5_connector.py` | connect, list/resolve symbols, fetch OHLCV, fetch symbol spec, graceful failure when MT5 is absent |
| `data/csv_loader.py` | load CSV/Parquet history with column mapping |
| `data/normalizer.py` | schema enforcement, UTC index, dedup, OHLC sanity, gap map, `is_closed` flag |
| `data/cache.py` | parquet cache + manifest, incremental append |
| `main.py` | `ingest` and `inspect` CLI commands |
| `tests/` | synthetic frames with deliberate duplicates/gaps/bad OHLC + real-file smoke test |
| `requirements.txt`, `.env.example`, `COST_AUDIT.md`, `README.md`, `CHANGELOG.md`, `KNOWN_ISSUES.md` | scaffolding |

**Explicitly out of scope for Phase 1:** swings, any SMC logic, probability, charts, decisions.

## 4. Key design points

- **MT5 is optional at import time.** `import MetaTrader5` is wrapped; on Linux/macOS
  or without a terminal the CLI still runs against CSV/Parquet. This matters because
  the MT5 Python package is Windows-only.
- **Symbols are never assumed.** `resolve_symbol("XAUUSD")` searches the broker's
  actual `symbols_get()` list for suffixed variants (`XAUUSDm`, `XAUUSD.m`, `XAUUSD_i`)
  and fails loudly with the candidates it found rather than guessing.
- **The forming bar is dropped** in every ingest unless `--include-forming` is passed,
  and it is then flagged `is_closed = False`.
- **Broker time → UTC** conversion uses a configured offset, verified once by comparing
  a known daily close against a reference; the offset is stored in the cache manifest.

## 5. Installation

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate     Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` (Phase 1): `pandas numpy pyarrow pydantic python-dotenv pytest`
plus `MetaTrader5` marked `sys_platform == "win32"`.

## 6. Run

```bash
python main.py ingest  --symbol XAUUSDm --tf M5 --bars 200000
python main.py ingest  --csv data/raw/XAUUSD_M5.csv --symbol XAUUSDm --tf M5
python main.py inspect --symbol XAUUSDm --tf M5
```

## 7. Expected output

```
XAUUSDm M5 | 198,431 bars | 2019-03-04 00:00 → 2026-08-15 20:55 UTC
duplicates removed: 12 | bad OHLC quarantined: 0 | gaps: 389 (weekend 372, other 17)
forming bar dropped: 2026-08-15 21:00
cached → data/cache/XAUUSDm/M5.parquet (14.2 MB)
```

## 8. Verification

1. `pytest tests/ -v` — all green, including the deliberately corrupt synthetic frames.
2. `inspect` reports 0 duplicates and a monotonic index on the cached file.
3. Spot-check 3 random bars against the MT5 terminal chart — OHLC and timestamp must match exactly.
4. Weekend gaps land on Fri→Sun boundaries; any weekday gap is listed for review.
5. Re-running `ingest` fetches only new bars and leaves existing rows byte-identical.

## 9. Common errors and fixes

| symptom | cause | fix |
|---|---|---|
| `ModuleNotFoundError: MetaTrader5` | non-Windows, or not installed | expected — use CSV mode, or install on Windows |
| `initialize() failed, error = (-10005)` | terminal closed / wrong path | open MT5, enable *Algo Trading*, set `MT5_PATH` in `.env` |
| Only 5,000 bars returned | terminal's max-bars chart setting | Tools → Options → Charts → raise "Max bars in chart", scroll the chart back to force download |
| Symbol not found | broker suffix | run `python main.py symbols --search XAU` and use the exact name |
| Timestamps off by 2–3 h | broker server time ≠ UTC | set `BROKER_UTC_OFFSET` in `.env`; the inspector prints the detected offset |
| `pyarrow` install failure | old pip | `pip install -U pip` |

## 10. Definition of done

Phase 1 is complete when a fresh clone can, on one command, produce a validated
parquet cache for at least one FX symbol and one metal symbol, the test suite passes,
and `inspect` output is reviewed and approved. Only then does Phase 2 (swing engine) start.
