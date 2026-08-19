# Phase 5 — Liquidity Pools & Sessions (BUILT)

Status: **complete**, 232 tests passing (193 from Phases 1–4 + 39 new).

---

## 1. What we built

An inventory of every level stops rest against — swing highs/lows, equal
highs/lows, previous day/week highs/lows, previous session highs/lows — each with
a recorded lifecycle, plus the session engine those session levels need.

## 2. Why

SMC's central claim is that price seeks liquidity. Nothing in the system could
name a liquidity level until now, which is why `mss_require_swept_origin` has
been sitting switched off since Phase 4. This is the inventory; Phase 6 turns
"price reached a pool" into a sweep event with magnitude and rejection.

## 3. Folder location

```
config/smc_rules.py            SessionWindow, SessionConfig, LiquidityConfig
liquidity/sessions.py          session assignment + per-instance high/low/range
liquidity/equal_levels.py      ATR-tolerance clustering of same-kind swings
liquidity/levels.py            LiquidityPool, LiquidityMap, build_liquidity()
tests/test_sessions.py         11 tests
tests/test_liquidity.py        23 tests
tests/test_no_lookahead.py     +5 liquidity oracle tests
main.py                        new `liquidity` command
```

## 4. Pool types, sides and lifecycle

| kind | level | side |
|---|---|---|
| `SWING_HIGH` / `SWING_LOW` | any confirmed swing | BUY_SIDE / SELL_SIDE |
| `EQH` / `EQL` | ≥2 same-kind swings within `equal_tolerance_atr × ATR` (0.10) formed within `equal_max_gap_bars` (50) | BUY_SIDE / SELL_SIDE |
| `PDH` / `PDL` | previous **completed** broker day's high/low | BUY_SIDE / SELL_SIDE |
| `PWH` / `PWL` | previous completed week's high/low | BUY_SIDE / SELL_SIDE |
| `SESSION_HIGH` / `SESSION_LOW` | previous completed session's high/low | BUY_SIDE / SELL_SIDE |

**Side convention:** buy-side liquidity sits **above** price (buy stops over old
highs), sell-side **below**. So a swing *high* is a BUY_SIDE pool — the thing a
seller wants swept before going short.

**Lifecycle**, all as-of honest:

```
INTACT    price has not traded beyond the level since the pool formed
SWEPT     a wick went beyond it (by > sweep_min_penetration_atr) but the bar
          closed back on the origin side
CONSUMED  a bar CLOSED beyond it -- the level is spent
```

A wick that only occurs on the same bar that closes through is **not** a sweep —
that is the level being taken out, and it is recorded as CONSUMED only. Phase 6
adds the rejection/reclaim test and sweep magnitude on top of this.

**Confirmation timing** is the whole game here:

- a swing pool is known at `swing.confirmed_at_index`, not when the high printed;
- an `EQH` pool is known when its **second** member confirms;
- `PDH`/`PDL` appear at the **first bar of the next day** — never while the day
  is still forming;
- a session pool appears at `session.end_index + 1`.

**Equal-level clusters grow.** A third equal high joins an existing cluster and
raises its price and strength *from its own confirmation bar onward*.
`price_at(t)` and `member_count_at(t)` only ever see members confirmed by `t`,
so a cluster that becomes a triple top later never rewrites what the pool looked
like before.

## 5. Strength

```
strength = base(kind)
         + strength_per_touch × min(touches, strength_max_touches)
         + strength_per_extra_member × (members - 2)        [equal levels]
         × (0.5 + 0.5 × tightness)                          [equal levels]
```

Bases: weekly 3.0 > daily 2.0 = equal 2.0 > session 1.5 > swing 1.0. Tightness is
1.0 when members are perfectly equal and 0.0 at the edge of tolerance. All values
are config, none are in code. This is a heuristic ordering, not a measured one —
Phase 13 onwards can test whether it predicts anything, and it should be replaced
if it doesn't.

## 6. Sessions

Windows are defined in a **named timezone**, so `Europe/London` windows shift
correctly across DST while `UTC` windows stay fixed. Defaults follow
`SMC_DEFINITIONS.md` §13 (Asian 00–07, London 07–12, NY AM 12–15, NY PM 15–20,
all UTC). A window whose end is at or before its start wraps past midnight and is
still treated as **one** session, not two split at midnight.

A session is never "complete" until its last bar is in the past — its high and
low are still moving, so they cannot be liquidity yet.

## 7. Run

```bash
python main.py liquidity --symbol XAUUSDm --tf M5 --last 4
python main.py liquidity --symbol XAUUSDm --tf M5 --equal-atr 0.25 --equal-gap 100
python main.py liquidity --symbol XAUUSDm --tf M5 --day-start 22 --no-sessions
python main.py liquidity --symbol XAUUSDm --tf M5 --as-of "2024-02-15 12:00"
pytest
```

## 8. Expected output (actual run, 18,539 bars)

```
pools found        : 2,414
known at bar 18538 : 2,414 {'SWING_LOW': 802, 'SWING_HIGH': 788,
                            'SESSION_HIGH': 260, 'SESSION_LOW': 260,
                            'EQH': 73, 'EQL': 55, 'PDH': 76, 'PDL': 76,
                            'PWH': 12, 'PWL': 12}
status             : {'CONSUMED': 2290, 'INTACT': 105, 'SWEPT': 19}
sessions           : {'ASIAN': 65, 'LONDON': 65, 'NY_AM': 65, 'NY_PM': 65}

bar 18538 (2024-03-29 20:55 UTC) close 2032.80000  session: -

buy-side liquidity above price (nearest first):
  kind                price     dist status    touch   str  origin
  SWING_HIGH     2034.03000  1.23000 INTACT        0  1.00  swing
  SESSION_HIGH   2037.80000  5.00000 INTACT        0  1.50  NY_PM 2024-03-29
```

95% of pools end up CONSUMED on this data. That is what a random walk should do
to every level given enough time, and it is worth remembering when Phase 6 starts
counting sweeps: **the base rate of "level eventually broken" is very high**, so a
sweep only means something relative to that.

Performance: 0.31 s for 18,539 bars including swing detection.

## 9. Verification

1. `pytest` — 232 passed. ✅
2. **Liquidity oracle**, 2 parameter sets: for every third bar, a fresh run over
   `frame[:t+1]` reproduces every known pool's price, status, touch count and
   member count exactly. ✅
3. **Status only moves forward** — INTACT → SWEPT → CONSUMED, never back. ✅
4. **Clusters only grow forward** — member counts are monotone in `t`. ✅
5. No pool is known before its creating bar; PDH is invisible while the day is
   still forming. ✅
6. DST: a `Europe/London` 08:00 window starts at 08:00 UTC on 29 Mar 2024 and at
   07:00 UTC on 1 Apr, after BST begins. ✅
7. An overnight window (22:00–04:00) produces one session instance, not two. ✅

## 10. Common errors and fixes

| symptom | cause | fix |
|---|---|---|
| No EQH/EQL pools | tolerance too tight for the instrument | raise `--equal-atr` (0.10 → 0.25); check ATR is seeded |
| Thousands of EQH pools | tolerance too wide | lower `--equal-atr`, shorten `--equal-gap` |
| PDH/PDL at the wrong time | broker day does not start at 00:00 UTC | set `--day-start` (many brokers roll at 22:00 or 00:00 server time) |
| Session levels look shifted | windows are in UTC but your broker is UTC+2/+3 | set `BROKER_UTC_OFFSET` at ingest, or define windows in a named tz |
| Everything is CONSUMED | expected on long histories | filter with `intact_at(t)`; the CLI already does |
| Session shows `-` | the bar is outside every configured window | 20:00–00:00 UTC is deliberately uncovered by the defaults |

## 11. Definition of done — met

Ten pool types with correct sides, an as-of honest three-state lifecycle,
growth-aware equal-level clusters, DST-correct sessions, and an oracle test
proving no pool or status is ever known early.

## 12. What Phase 6 builds on this

`liquidity/sweeps.py`: a sweep is *pool exists* + *price trades through it* +
*price rejects/reclaims*. It will classify `BUY_SIDE_SWEEP` / `SELL_SIDE_SWEEP`
and measure sweep magnitude in ATR, rejection size, close location, bars to
reject and distance from structure — the feature block that
`SWEEP_MSS_FVG` needs. That is also when `mss_require_swept_origin` can finally
be switched on, completing the MSS definition.
