# Phase 2 — Swing Detection (BUILT)

Status: **complete**, 113 tests passing (74 from Phase 1 + 39 new).

---

## 1. What we built

The swing engine — the layer that turns a candle table into structure points, and
the place where the project's no-repainting guarantee is actually enforced.

## 2. Why

Every later concept is defined in terms of swings: structure (HH/HL/LH/LL) is a
sequence of them, BOS/CHOCH/MSS are breaks of them, liquidity pools sit on them,
order blocks are found by walking back from them. If swings move after the fact,
every statistic built on top is measuring a future the trader didn't have.

## 3. Folder location

```
common/indicators.py     true_range, wilder_atr (strictly causal)
config/smc_rules.py      SwingConfig + SMCRules, with a rules_hash
structure/swings.py      SwingPoint, SwingSeries, detect_swings()
tests/test_indicators.py
tests/test_swings.py
tests/test_no_lookahead.py   the oracle test
main.py                  new `swings` command
```

## 4. The two indices (the whole point)

```
formed_at_index      the bar the swing geometrically sits on
confirmed_at_index   formed_at_index + swing_right  -- the first bar it could be KNOWN
```

A swing with `swing_right=3` that formed at bar 100 does not exist to the system
until bar 103. `SwingSeries.as_of(t)` returns the chain exactly as it stood at bar
`t`, and **that is what the oracle test compares against a fresh run over
`frame[:t+1]`**.

Supersession works the same way. When a higher high replaces the current one, the
old swing is not deleted — it gets `superseded_at_index` = the *new* swing's
confirmation bar. So at bar 45 the old high is still the answer, and only at bar 46
does the new one take over. History stays reproducible; nothing is rewritten.

## 5. Rules implemented (docs/SMC_DEFINITIONS.md §1)

| rule | default | behaviour |
|---|---|---|
| mode | `FRACTAL` | strict left, non-strict right → a plateau resolves to its first bar |
| | `FIXED_LOOKBACK` | window extremum, ties both sides |
| | `ATR_ADAPTIVE` | window scales with ATR ÷ its rolling median, clamped 0.5–2.5× |
| `swing_left/right` | 3 / 3 | window size |
| `min_swing_atr` | 0.5 | distance from the previous **live opposite** swing, in ATR |
| ATR not seeded | — | candidate rejected (`NO_ATR`), never accepted unfiltered |
| collapse | — | same-kind candidate must be more extreme, else `NOT_EXTREME` |
| gaps | flag | `spans_gap` recorded; `reject_across_gaps` available but off |

Rejected candidates are kept with their reason. `series.reject_counts()` is how you
tune `min_swing_atr` — silently dropped candidates would hide bugs.

## 6. Run

```bash
python main.py swings --symbol XAUUSDm --tf M5 --last 6
python main.py swings --symbol XAUUSDm --tf M5 --min-atr 2.0 --left 5 --right 5
python main.py swings --symbol XAUUSDm --tf M5 --as-of "2024-03-29 18:00"
pytest
```

## 7. Expected output (actual run, 18,539 bars)

```
symbol / timeframe : XAUUSDm M5
bars analysed      : 18,539
mode               : FRACTAL (left=3, right=3, min_swing_atr=0.5)
rules hash         : 76f8a3af45e19033...
swings accepted    : 3,168
candidates rejected: 367 {'NO_ATR': 2, 'NOT_EXTREME': 322, 'ATR_FILTER': 43}
state at bar       : 18538 (2024-03-29 20:55 UTC)
live chain length  : 2,802
chain alternates   : True

last 6 live swings:
  kind  formed (UTC)           price  conf. bar  ATR str
  HIGH  2024-03-29 17:45 2035.49000      18503     1.26
  LOW   2024-03-29 18:15 2032.87000      18509     2.06
  ...
superseded (kept for history, not erased): 366
```

Tightening to `--min-atr 2.0 --left 5 --right 5` cuts 3,168 swings to 1,590 — that
knob is the difference between "every wiggle" and "structure", and Phase 3 will care
a great deal about where it sits.

Performance: 0.07 s for 18,539 bars (FRACTAL), 0.14 s (ATR_ADAPTIVE).

## 8. Verification

1. `pytest` — 113 passed. ✅
2. **Oracle test**, 5 parameter sets × 260 bars: for every bar `t`, a fresh run over
   `frame[:t+1]` produces exactly the full run's `as_of(t)`. ✅
3. **Live-replay test**: feeding bars one at a time, the recorded state of every
   earlier bar is never rewritten by a later bar. ✅
4. ATR causality: `wilder_atr(frame[:k]) == wilder_atr(frame)[:k]`. ✅
5. Hand-built geometry: the triangle's peak is found at the right index, at the
   right price, confirmed exactly `swing_right` bars later. ✅
6. Live chain alternates HIGH/LOW at every checkpoint, on 400 bars of random walk. ✅
7. A forming bar raises rather than silently producing structure. ✅

## 9. Common errors and fixes

| symptom | cause | fix |
|---|---|---|
| `frame contains an unclosed bar` | passed a live frame | use `closed_bars(frame)` — the guard is deliberate |
| Thousands of swings on M5 | `min_swing_atr` too low for the instrument | raise `--min-atr` (2.0 is a reasonable structural setting) or widen `--left/--right` |
| Almost no swings | threshold too high, or ATR unseeded on a short frame | lower `--min-atr`; ensure ≥ `atr_period + left + right` bars |
| `NO_ATR` rejections at the start | expected — the first `atr_period` bars have no ATR | ignore, or pre-load extra warm-up bars |
| Swing prices look wrong on FX | you compared against a chart using bid/ask differently | the frame stores what the broker exported; check the symbol's `digits` |

## 10. Definition of done — met

Swings are detected in three modes, carry both indices, never repaint under the
oracle test, and are inspectable from the CLI at any historical bar.

## 11. What Phase 3 builds on this

`market_structure.py` consumes `SwingSeries.as_of(t)` and labels the chain
HH/HL/LH/LL, maintaining `structural_high/low`, `protected_high/low`, internal vs
external structure, and a `BULLISH / BEARISH / RANGE` bias — using only swings that
were confirmed by bar `t`, so the bias timeline is itself non-repainting.
