# Phase 1 — Data Ingestion & Normalization (BUILT)

Status: **complete**, 74 tests passing. Verified end-to-end on Linux in CSV mode
(the MT5 path is Windows-only and needs your terminal to verify — see §8).

---

## 1. What we built

A data layer that turns "whatever the broker gives us" into one clean, trustworthy,
reusable candle table, plus a local cache so history is never re-downloaded.

## 2. Why first

Every later module (swings, structure, probability, backtests) reads this table. A
silent flaw here — a duplicated bar, a timezone shift, a half-formed last candle —
corrupts every statistic downstream and is nearly invisible once buried. Cheap to
get right now, expensive later.

## 3. Deliverables (all present)

| file | purpose |
|---|---|
| `config/settings.py` | paths, timeframe registry, broker offset, `.env` loading, Pydantic-validated and frozen |
| `data/normalizer.py` | schema gate: UTC index, dedup, OHLC sanity, gap map, `is_closed`; `validate_frame()` + `closed_bars()` |
| `data/csv_loader.py` | CSV/TSV/Parquet, separator sniffing, MT5 `<DATE>`/`<TIME>` export format, column mapping |
| `data/mt5_connector.py` | read-only MT5: connect, symbols, `resolve_symbol`, `SymbolSpec`, chunked bar fetch, server-offset detection |
| `data/cache.py` | parquet cache + JSON manifest, incremental merge, content hash |
| `main.py` | `ingest` · `inspect` · `symbols` · `status` |
| `tools/make_synthetic_csv.py` | generates an MT5-style export so the pipeline runs without MT5 |
| `tests/` | 74 tests over frames with known-correct answers |
| `requirements.txt`, `.env.example`, `.gitignore`, `pytest.ini` | scaffolding |

**Out of scope, as agreed:** swings, any SMC logic, probability, charts, decisions.

## 4. Key design points

- **MT5 is optional at import time.** `import MetaTrader5` is wrapped; on Linux/macOS
  or without a terminal the CLI still runs against CSV/Parquet. The engine never
  imports MT5 at module scope.
- **READ ONLY by construction.** No order function is exposed from the connector, and
  a test asserts it (`test_no_order_functions_are_exposed`).
- **Symbols are never assumed.** `resolve_symbol("XAUUSD")` searches the broker's real
  `symbols_get()` list across 17 suffix variants and, on failure, raises with the
  similar names the broker actually offers.
- **The forming bar is dropped** unless `--include-forming` is passed, in which case it
  is kept but flagged `is_closed = False`. `closed_bars()` is the only view the SMC
  engine will ever be given.
- **Bad data is quarantined, never repaired.** Impossible OHLC rows are written to
  `data/quarantine/` as CSV and excluded; the hole they leave is then correctly
  reported as a gap.
- **Missing bars are recorded, never forward-filled.** Weekend gaps (any span crossing
  a Saturday) are separated from weekday gaps, which are printed for review.
- **Broker time → UTC.** Naive timestamps and MT5 epoch integers are shifted by
  `BROKER_UTC_OFFSET`; tz-aware timestamps are converted, never double-shifted. In MT5
  mode the offset is detected from a live tick and a mismatch with `.env` logs a warning.

## 5. Installation

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate     Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # optional — defaults work with no credentials
```

## 6. Run

```bash
python main.py status
python main.py symbols --search XAU                                   # MT5 only
python main.py ingest  --symbol XAUUSDm --tf M5 --bars 200000         # MT5
python main.py ingest  --symbol XAUUSDm --tf M5 --csv data/raw/XAUUSDm_M5.csv --digits 2
python main.py inspect --symbol XAUUSDm --tf M5 --rows 3
pytest
```

No MT5 handy? Generate a sample export first:

```bash
python tools/make_synthetic_csv.py --out data/raw/XAUUSDm_M5.csv
```

## 7. Expected output (actual run, 3 months of M5 with two injected defects)

```
$ python main.py ingest --symbol XAUUSDm --tf M5 --csv data/raw/XAUUSDm_M5.csv --digits 2
XAUUSDm M5 | 18,539 bars | 2024-01-01 00:00 -> 2024-03-29 20:55 UTC
duplicates removed: 1 | bad OHLC quarantined: 1 | gaps: 13 (weekend 12, other 1)
quarantined rows written to data/quarantine/XAUUSDm_M5_20260818T213017Z.csv

non-weekend gaps needing review (1, showing up to 10):
  2024-01-04 02:55 -> 2024-01-04 03:05 (1 bars missing)
cached -> data/cache/XAUUSDm/M5.parquet (0.4 MB, 18,539 bars total)
```

That weekday gap is the hole left by the quarantined bar — exactly the behaviour we
want: a removed bad bar becomes a visible gap, not a silent one.

```
$ python main.py inspect --symbol XAUUSDm --tf M5
rows              : 18,539
range (UTC)       : 2024-01-01 00:00 -> 2024-03-29 20:55
schema validation : PASS
duplicate index   : 0
monotonic index   : True
closed bars       : 18,539 of 18,539
gap_before rows   : 13
modal bar spacing : 0 days 00:05:00 (expected 0:05:00)
content hash      : 3e8268dd30a4dcc4...
```

## 8. Verification

Checks 1, 2 and 5 were run here and pass. Checks 3 and 4 need your broker's data.

1. `pytest` — 74 passed.
2. `inspect` reports `PASS`, 0 duplicates, monotonic index. ✅
3. **You do this:** spot-check 3 random bars against your MT5 chart — OHLC and
   timestamp must match exactly. If they are shifted by a whole number of hours, your
   `BROKER_UTC_OFFSET` is wrong.
4. **You do this:** confirm the weekday gaps `ingest` prints are real broker outages or
   holidays, not silently missing history.
5. Re-running `ingest` leaves rows byte-identical — verified: the manifest content hash
   was `3e8268dd30a4dcc4…` before and after, with row count unchanged at 18,539. ✅

## 9. Common errors and fixes

| symptom | cause | fix |
|---|---|---|
| `ModuleNotFoundError: MetaTrader5` | non-Windows, or not installed | expected — use `--csv`, or `pip install MetaTrader5` on Windows |
| `MT5 initialize() failed: (-10005)` | terminal closed / wrong path | open MT5, enable *Algo Trading*, set `MT5_PATH` in `.env` |
| Only ~5,000 bars returned | terminal's max-bars setting | Tools → Options → Charts → raise "Max bars in chart", open that chart and scroll back to force the download |
| `symbol 'XAUUSD' not found` | broker suffix | the error lists similar real names; or run `python main.py symbols --search XAU` |
| Timestamps off by 2–3 h | broker server time ≠ UTC | set `BROKER_UTC_OFFSET`; MT5 mode warns when `.env` disagrees with the terminal |
| `bad OHLC quarantined: N` with large N | wrong column mapping | check the header; pass `--csv` with a correctly exported file, or use `column_map` |
| Many "non-weekend gaps" | incomplete history | scroll the chart back in MT5 and re-ingest; the merge is incremental |
| `pyarrow` install failure | old pip | `pip install -U pip` |

## 10. Definition of done — met

A fresh clone can, in one command, produce a validated parquet cache; the test suite
passes; `inspect` reports PASS. Remaining before Phase 2 is **your** verification of
checks 3 and 4 against real broker data.

## 11. What Phase 2 will build on this

`swings.py` will consume `closed_bars(read_bars(symbol, tf))` and emit swing points
carrying `formed_at_index` / `confirmed_at_index`, with the no-repaint oracle test
described in ARCHITECTURE.md §5.
