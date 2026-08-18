# Phase 3 — Market Structure (BUILT)

Status: **complete**, 141 tests passing (113 from Phases 1–2 + 28 new).

---

## 1. What we built

The layer that turns a list of swings into a structural reading: every swing
labelled HH / HL / LH / LL (or EQH / EQL), the four levels that matter
(structural high/low, protected high/low), and a per-bar bias timeline.

## 2. Why

"Bias" is the single most reused input in the whole system — it feeds the HTF
filter, the setup score (15 of 100 points), and the similarity key that
probabilities are grouped by. If bias is computed from swings that weren't yet
knowable, every probability downstream is contaminated. So bias gets the same
as-of treatment swings got, and its own oracle test.

## 3. Folder location

```
config/smc_rules.py               StructureConfig (+ internal_swing_config())
structure/market_structure.py     labels, levels, bias, MarketStructure
tests/test_market_structure.py    23 tests
tests/test_no_lookahead.py        +5 structure oracle tests
main.py                           new `structure` command
```

## 4. The rules, exactly

**Labels** — each swing is compared to the previous swing *of the same kind*:

| condition | label |
|---|---|
| high above the previous high | `HH` |
| high below the previous high | `LH` |
| low above the previous low | `HL` |
| low below the previous low | `LL` |
| within `equal_tolerance_atr × ATR` (default 0.05) | `EQH` / `EQL` |
| nothing to compare against yet | `FIRST_HIGH` / `FIRST_LOW` |

A swing that *supersedes* another is still labelled against the one it replaced —
a higher high is an `HH`, which is what a trader would have called it at the time.

**Levels**

- `structural_high` / `structural_low` — the most recently confirmed swing of each
  kind. (A superseded swing can never be one: supersession only happens between
  consecutive same-kind swings, so the latest confirmed swing is always the live one.)
- `protected_low` — the last low that formed **before** the current structural high:
  the low that created that high, and the level whose loss invalidates the leg.
  `protected_high` mirrors it.

**Bias** (`bias_source = SWING_SEQUENCE`)

```
BULLISH   last high is HH  AND  last low is HL
BEARISH   last high is LH  AND  last low is LL
RANGE     anything else, OR dealing range < range_atr_mult × ATR (default 2.0)
```

Two deliberate choices:

- **Non-sticky.** When the sequence stops being clean — an HH lands while the low
  is still LL — the answer is `RANGE`, not "still bearish because it was bearish".
  Transitional states are real and the engine should say so.
- **Narrow-range veto.** A 1.9 × ATR dealing range is noise whatever the labels
  say. Seen live: at 2024-02-15 12:00 the labels read LH/LL but the range was
  1.86 × ATR, so bias was correctly `RANGE`.

**Scope note.** Phase 3 derives bias from the swing sequence alone; BOS/CHOCH/MSS
do not exist yet. `bias_source` records which rule produced the value, and Phase 4
will add `BOS_CONFIRMED` under the same field rather than inventing a second one.

**Internal structure** is the same algorithm on a finer swing setting
(`internal_left/right`, default 1/1), exposed as `structure.internal`.

## 5. Run

```bash
python main.py structure --symbol XAUUSDm --tf M5 --min-atr 2.0 --left 5 --right 5
python main.py structure --symbol XAUUSDm --tf M5 --as-of "2024-02-15 12:00"
python main.py structure --symbol XAUUSDm --tf M5 --range-atr 0 --no-internal
pytest
```

## 6. Expected output (actual run, 18,539 bars)

```
swings / labels    : 1,590 / 1,590
label counts       : {'HH': 429, 'HL': 330, 'LL': 451, 'LH': 338, 'EQL': 20, 'EQH': 20, ...}
bias share         : {'BEARISH': '30.1%', 'BULLISH': '28.2%', 'RANGE': '41.8%'}
bias changes       : 855

state at bar 18538 (2024-03-29 20:55 UTC)
bias             : RANGE (SWING_SEQUENCE)
structural high  : 2034.03000 @ 2024-03-29 20:05
structural low   : 2031.76000 @ 2024-03-29 20:20
protected high   : 2034.03000 @ 2024-03-29 20:05
protected low    : 2031.60000 @ 2024-03-29 19:35
last labels      : high=LH low=HL
dealing range    : 2.27000 (2.05 x ATR)

last 5 bias changes:
  2024-03-29 10:55    RANGE -> BULLISH  (high=HH, low=HL, range=3.75xATR)
  2024-03-29 14:30  BULLISH -> RANGE    (high=HH, low=LL, range=5.40xATR)
  2024-03-29 15:45    RANGE -> BEARISH  (high=LH, low=LL, range=2.66xATR)
```

**Parameter sensitivity** (same data, three swing settings):

| swing params | labels | bias changes | bias share |
|---|---|---|---|
| left=3 right=3 min_atr=0.5 | 3,168 | 1,671 | 19% / 18% / 63% |
| left=5 right=5 min_atr=2.0 | 1,590 | 855 | 30% / 28% / 42% |
| left=8 right=8 min_atr=3.0 | 780 | 356 | 31% / 28% / 42% |

Read that table as a warning, not a feature: bias-change frequency is a direct
function of the swing setting, so any statistic conditioned on bias is too. This is
exactly the parameter-sensitivity risk in KNOWN_ISSUES #3, and Phase 19 will report
it as a surface rather than a single tuned point.

Performance: 0.13 s for 18,539 bars including swing detection.

## 7. Verification

1. `pytest` — 141 passed. ✅
2. **Structure oracle**, 3 parameter sets × 220 bars: for every bar `t`, a fresh
   `build_structure(frame[:t+1]).current` matches the full run's `state_at(t)` —
   bias, both structural levels, both protected levels, both labels. ✅
3. **Labels are fixed at confirmation** — a swing's HH/HL/LH/LL never gets
   rewritten by later bars. ✅
4. **Two independent bias paths agree**: the fast forward pass (`bias_at`) and the
   from-scratch recomputation (`state_at`) match on every bar of 300. ✅
5. Hand-built sequences: uptrend → all HH/HL, downtrend → all LH/LL, and the
   reversal fixture produces exactly RANGE→BEARISH→RANGE→BULLISH. ✅
6. An HH alone does not turn bias bullish — the low must confirm too. ✅

## 8. Common errors and fixes

| symptom | cause | fix |
|---|---|---|
| Bias flips constantly | swing setting too fine for the timeframe | raise `--min-atr` / widen `--left --right`; see the sensitivity table |
| Everything is RANGE | `--range-atr` too high for the instrument's ATR | lower it (`0` disables the veto entirely) |
| No EQH/EQL ever | `--equal-atr 0` | raise the tolerance (0.05–0.15 ATR is typical) |
| `protected_low` is None | fewer than one high and one low confirmed yet | expected at the start of history |
| Internal structure looks identical to external | `internal_left/right` too close to the external setting | drop them to 1/1 or lower `internal_min_swing_atr` |

## 9. Definition of done — met

Labels, four levels and a bias timeline, all as-of honest, cross-checked by two
independent code paths, and inspectable at any historical bar from the CLI.

## 10. What Phase 4 builds on this

`bos.py`, `choch.py`, `mss.py` consume `StructureState.structural_high/low` and
`protected_high/low` at bar `t` and detect closes through them:

- **BOS** — close beyond a structural level *in the direction of bias* (continuation).
- **CHOCH** — first close through the protected level *against* bias (warning; bias → RANGE).
- **MSS** — CHOCH **plus** displacement plus a close break plus a swept-liquidity
  origin (confirmed reversal; flips bias).

At that point `bias_source` becomes `BOS_CONFIRMED` and the bias timeline gains the
stickiness Phase 3 deliberately does without.
