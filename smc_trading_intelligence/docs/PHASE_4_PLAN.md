# Phase 4 — BOS / CHOCH / MSS (BUILT)

Status: **complete**, 193 tests passing (141 from Phases 1–3 + 52 new).

---

## 1. What we built

The three structural break events, kept strictly separate, plus the first
version of displacement (which MSS depends on) and a break-confirmed bias
timeline that replaces the Phase 3 label-only one.

## 2. Why

This is where SMC usually goes wrong: "BOS", "CHOCH" and "MSS" get used
interchangeably, so nobody can say what a backtest actually measured. Here they
are three event types with three definitions, three effects on bias, and their
own tests. `SWEEP_MSS_FVG` — the headline setup family — is only meaningful if
MSS means one specific thing.

## 3. Folder location

```
config/smc_rules.py           BOSMode, DisplacementConfig, BreakConfig
structure/displacement.py     displacement scoring (v1: 3 of 4 components)
structure/breaks.py           BreakEvent, BreakSeries, detect_breaks()
structure/market_structure.py + iter_levels(), attach_breaks(), with_breaks=True
tests/test_displacement.py    22 tests
tests/test_breaks.py          25 tests
tests/test_no_lookahead.py    +5 break oracle tests
main.py                       new `breaks` command
```

## 4. The three definitions, and what each one does to bias

| event | trigger | effect on bias |
|---|---|---|
| **BOS** | close beyond a **structural** level, in the direction of bias (or from RANGE) | sets bias to that direction — **confirms, never flips** |
| **CHOCH** | first close through the **protected** level, **against** bias | bias → **RANGE**. A warning, not an entry |
| **MSS** | a CHOCH **plus** displacement ≥ threshold **plus** a close break of a level with real structure behind it | bias **flips** to the new direction |

Every MSS also appears as a CHOCH event (with `mss.choch_index` linking them);
no CHOCH is automatically an MSS; BOS is a different class entirely.

**Precedence within a bar: MSS > CHOCH > BOS.** A bar that reverses the trend
does not also "continue" it. This was a real bug found while building: the same
bar emitted CHOCH, MSS *and* a BOS on the same already-broken level. A level is
now consumed when broken, so it cannot produce a second event.

**Pending CHOCH.** A CHOCH without displacement stays pending for
`mss_confirm_window` bars (default 10). If a later bar closes through the same
level *with* displacement, the MSS confirms then. Otherwise it expires and is
counted (`expired_choch`) — on real data 60 of 180 CHOCHs expired, which is a
useful diagnostic in itself.

**BOS modes:** `CLOSE_ONLY` (default — a wick through a level is liquidity being
taken, not structure breaking), `WICK_OR_CLOSE`, `DISPLACEMENT_CONFIRMATION`.

**Gap guard:** a bar flagged `gap_before` cannot confirm a break
(`reject_on_gap_bar`, default on) — this is the ARCHITECTURE §4 rule that
Phase 2 deliberately deferred, now in force where it belongs.

## 5. Displacement (v1)

Score in [0, 1] from three components, all ATR-normalised:

```
body_component   |close - open| / ATR, full credit at 1.0 ATR    weight 0.50
range_component  (high - low)  / ATR, full credit at 1.5 ATR     weight 0.25
close_location   where the close sits in the range, from 0.70 up weight 0.25
imbalance        does the move leave an FVG                      weight 0.00  ← Phase 8
```

`NONE < 0.35 ≤ WEAK < 0.55 ≤ MODERATE < 0.75 ≤ STRONG`.

The fourth component needs FVGs (Phase 8), so its weight is **0.0 today and the
other three sum to 1.0**. Turning it on later is a config change that alters
`rules_hash` — visible and reproducible — rather than a silent redefinition of
what "STRONG" means. Phase 7 completes this module with multi-bar runs.

Direction matters: a strong down bar scores ~0 as *bullish* displacement.

## 6. Run

```bash
python main.py breaks --symbol XAUUSDm --tf M5 --last 6
python main.py breaks --symbol XAUUSDm --tf M5 --bos-mode DISPLACEMENT_CONFIRMATION
python main.py breaks --symbol XAUUSDm --tf M5 --min-displacement 0.75 --mss-window 20
python main.py breaks --symbol XAUUSDm --tf M5 --as-of "2024-02-15 12:00"
pytest
```

## 7. Expected output (actual run, 18,539 bars)

```
bos mode           : CLOSE_ONLY
mss threshold      : 0.55 (window 10 bars)
events             : 742 {'BOS_BULLISH': 218, 'BOS_BEARISH': 224,
                          'CHOCH_BEARISH': 90, 'CHOCH_BULLISH': 90,
                          'MSS_BULLISH': 59, 'MSS_BEARISH': 61}
expired CHOCH      : 60
bias share (breaks): {'BEARISH': '45.4%', 'BULLISH': '44.4%', 'RANGE': '10.2%'}

state at bar 18538 (2024-03-29 20:55 UTC)
bias             : BEARISH (BOS_CONFIRMED)
```

Compare with Phase 3 on the same bars: swing-sequence bias was 30% / 28% / 42%.
The break-confirmed timeline spends 10% in RANGE instead of 42% — it commits to
a direction at the BOS and holds it until a CHOCH, which is the stickiness
Phase 3 deliberately did without. Both readings remain available:
`bias_at()` (break-confirmed) and `swing_sequence_bias_at()` (labels only).

Performance: 0.22 s for 18,539 bars, swings + structure + breaks.

## 8. Verification

1. `pytest` — 193 passed. ✅
2. **Break oracle**, 3 parameter sets × 200 bars: for every bar `t`, a fresh run
   over `frame[:t+1]` produces exactly the events the full run knew at `t`, with
   the same bias. An event's `index` really is the first bar it could be known. ✅
3. **Break-confirmed bias is reproducible** at every checkpoint bar. ✅
4. **Displacement never revises**: scoring bar `t` from a truncated frame gives
   the same number as from the full frame. ✅
5. Hand-built scenario: warm-up → 2 bullish BOS → wide bar through the protected
   low → CHOCH + MSS on that bar → bias flips BEARISH. Remove the body from that
   one bar and the MSS correctly does not fire — same geometry, same CHOCH. ✅
6. No bar ever emits both a reversal and a continuation. ✅
7. A gap bar cannot confirm a break. ✅

## 9. Common errors and fixes

| symptom | cause | fix |
|---|---|---|
| Hundreds of BOS on M5 | every minor swing is structural | raise `--min-atr` / widen `--left --right`, or use `--bos-mode DISPLACEMENT_CONFIRMATION` |
| No MSS ever fires | threshold too high for the instrument's bar shapes | lower `--min-displacement`; check `expired CHOCH` — if high, the moves lack thrust |
| MSS fires on every CHOCH | threshold too low | raise it; 0.55 (MODERATE) is the default, 0.75 is STRONG |
| `expired CHOCH` is very high | CHOCHs are not being followed by displacement | widen `--mss-window`, or accept that this market reverses slowly |
| Bias never leaves RANGE | not enough confirmed swings, or every break lands on a gap bar | check `swings` output first; inspect gaps with `inspect` |
| Displacement always ~0 | bars have tiny bodies relative to ATR (common on synthetic data) | verify with real broker data; the metric is per-bar body vs ATR |

## 10. Definition of done — met

Three separate event types with distinct definitions and bias effects, a
displacement score with a documented staged rollout, a break-confirmed bias
timeline, and an oracle test proving no event is ever known before its bar.

## 11. Deviation from the planned tree

`bos.py`, `choch.py`, `mss.py` were planned as three modules. They share one
forward pass over bars with one bias state machine, so splitting them would have
meant three modules mutating each other's state. They live in `structure/breaks.py`
with a separate predicate, event type and test group each. `structure/displacement.py`
was added early (Phase 7's slot) because MSS cannot be defined without it.

## 12. What Phase 5 builds on this

`liquidity/levels.py` and `equal_levels.py`: buy-side and sell-side pools from
swing highs/lows, equal highs/lows within an ATR tolerance, PDH/PDL, PWH/PWL and
session highs/lows — each with `created_at`, `touch_count` and a
`INTACT / SWEPT / CONSUMED` lifecycle. That is what finally lets
`mss_require_swept_origin` be switched on (Phase 6), completing the MSS
definition in `SMC_DEFINITIONS.md` §5.
