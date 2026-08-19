# Exact SMC Definitions (v0.1 rule freeze)

Every definition below is deterministic, computable from closed bars only, and
tied to a config key. Where the SMC community disagrees, the disagreement is
noted and the alternative is exposed as a config mode rather than an opinion.

Notation: bars are indexed `0..n-1`; bar `i` has `O_i H_i L_i C_i`.
`ATR_i` = Wilder ATR over `atr_period` (default 14) computed on bars `<= i`.

---

## 1. Swing points

**Fractal mode (default).** Bar `i` is a *swing high* if
`H_i > H_j` for all `j in [i-L, i-1]` and `H_i >= H_j` for all `j in [i+1, i+R]`,
with `L = swing_left` (default 3), `R = swing_right` (default 3).
Swing low is the mirror with lows.

- `formed_at_index = i`, `confirmed_at_index = i + R`.
- Status is `DEVELOPING` for `t < i+R`, `CONFIRMED` at `t >= i+R`. Only CONFIRMED
  swings feed structure. This is the anti-repaint anchor.
- **ATR filter:** a swing is discarded unless
  `|H_i - (opposite adjacent swing)| >= min_swing_atr * ATR_i` (default 0.5).
- **Strength** = `(H_i - min(L over the L+R window)) / ATR_i`, stored for features.
- Alternative modes: `FIXED_LOOKBACK` (window extremum, ties allowed both sides),
  `ATR_ADAPTIVE` (L and R scale with ATR ÷ its own rolling median, clamped to
  `[adaptive_min_scale, adaptive_max_scale]`). Config: `swing.mode`.
- **Plateau rule.** FRACTAL is strict on the left and non-strict on the right, so a
  run of equal highs resolves to its **first** bar — the print that created the
  level. The later equal highs are not lost; they become equal-high liquidity (§7).
- Consecutive same-type swings without an intervening opposite swing are collapsed
  to the more extreme one (prevents swing spam in trends). **The collapsed swing is
  never deleted:** it records `superseded_at_index` = the confirmation bar of the
  swing that replaced it, so "the chain as known at bar t" stays reproducible. A
  same-kind candidate that is *not* more extreme is rejected outright
  (`NOT_EXTREME`) and never enters the chain.
- **ATR filter reference.** The "opposite adjacent swing" is the most recent *live*
  swing of the opposite kind. When none exists yet (start of history), the fallback
  is the candidate's own window excursion. If ATR is not yet seeded and
  `min_swing_atr > 0`, the candidate is rejected (`NO_ATR`) rather than accepted
  unfiltered.
- **Gaps.** A swing whose window spans missing bars is *flagged* (`spans_gap`), not
  discarded — otherwise every legitimate swing around a weekend close would vanish.
  `swing.reject_across_gaps` (default `false`) makes the stricter choice available.
  This narrows the blanket rule in ARCHITECTURE.md §4, which still applies to
  break/displacement patterns from Phase 4 on.
- Rejected candidates are retained with their reason (`ATR_FILTER`, `NO_ATR`,
  `NOT_EXTREME`, `SPANS_GAP`) — silently dropped candidates hide bugs and make
  `min_swing_atr` impossible to tune.

---

## 2. Market structure

Sequence of confirmed alternating swings labelled:

- `HH` — swing high above previous swing high
- `HL` — swing low above previous swing low
- `LH` — swing high below previous swing high
- `LL` — swing low below previous swing low

Each swing is labelled against the previous swing **of the same kind**, at its own
confirmation bar. `EQH`/`EQL` when within `equal_tolerance_atr × ATR` (default 0.05);
`FIRST_HIGH`/`FIRST_LOW` when there is nothing to compare against yet. A swing that
supersedes another is labelled against the swing it replaced — a higher high is an `HH`.

State machine maintains:

- `structural_high` / `structural_low` — the most recently confirmed swing of each
  kind: the swing that must break to change external structure. A superseded swing
  can never occupy this slot (supersession only happens between consecutive
  same-kind swings, so the latest confirmed swing is always the live one).
- `protected_high` / `protected_low` — the last opposite-kind swing that formed
  *before* the current structural level: the low that created the current high (and
  mirror). This is the level whose break invalidates the current leg. From Phase 7
  this is refined to "before the last displacement leg" once displacement exists.
- `internal_structure` — same algorithm with `swing_left/right = 1` (or on the LTF).
- `external_structure` — the default parameters.
- `bias ∈ {BULLISH, BEARISH, RANGE}` with a `bias_source` recording how it was derived:
  - **`SWING_SEQUENCE` (labels only):** `BULLISH` when the last high is `HH`
    **and** the last low is `HL`; `BEARISH` when `LH` **and** `LL`; `RANGE`
    otherwise. Deliberately **non-sticky** — a broken sequence reads `RANGE`, not
    a stale trend. Always available via `swing_sequence_bias_at()`.
  - **`BOS_CONFIRMED` (default once breaks are attached):** a BOS sets bias to its
    direction and it holds there until a CHOCH returns it to `RANGE` or an MSS
    flips it. This is the sticky reading; `bias_at()` returns it whenever a
    `BreakSeries` is attached (`build_structure(..., with_breaks=True)`).
  - In both: `RANGE` whenever the dealing range width `< range_atr_mult * ATR`
    (default 2.0), regardless of labels.

Micro noise is excluded by the swing ATR filter, so not every wiggle is a structure event.
Bias-change frequency is nonetheless a direct function of the swing setting — see the
sensitivity table in `PHASE_3_PLAN.md` §6 and KNOWN_ISSUES #3.

---

## 3. BOS — Break of Structure (continuation)

A **bullish BOS** at bar `i` requires all of:
1. Current bias is `BULLISH` **or** `RANGE`.
2. `structural_high` `SH` exists and is CONFIRMED.
3. Break condition per `bos.mode`:
   - `CLOSE_ONLY` (default): `C_i > SH.price`.
   - `WICK_OR_CLOSE`: `H_i > SH.price`.
   - `DISPLACEMENT_CONFIRMATION`: `C_i > SH.price` **and** bar `i` qualifies as displacement (§6).
4. Bar `i` is closed and `gap_before_i == False`.

Bearish BOS is the exact mirror on `structural_low` with `C_i < SL.price`.

Recorded: `timestamp, direction, broken_level_price, broken_level_time, break_price,
timeframe, displacement_score, body_atr, tick_volume, confirmed_at_index = i`.

**BOS never changes the bias — it confirms it.**

---

## 4. CHOCH — Change of Character (first opposing break)

A **bearish CHOCH** at bar `i` requires:
1. Current bias is `BULLISH`.
2. Price closes below the most recent **protected low** (`C_i < protected_low.price`).
3. It is the *first* such break since the bias became BULLISH.

Effect: bias → `RANGE` (transitional), and the CHOCH level is stored.
CHOCH is a **warning**, not an entry trigger. Bullish CHOCH is the mirror.

Difference from BOS: BOS breaks a level in the direction of bias; CHOCH breaks a
level against it.

---

## 5. MSS — Market Structure Shift (confirmed reversal)

A **bearish MSS** at bar `i` requires **all** of:
1. A bearish CHOCH has occurred at or before `i` (same protected low), **and**
2. The breaking leg qualifies as displacement (§6) — `displacement_score >= mss.min_displacement`, **and**
3. The break is a **close** through the level (`C_i < level`, regardless of `bos.mode`), **and**
4. The move originated from a swept liquidity level or a premium-zone high
   within `mss.origin_lookback` bars — **not yet enforced**: liquidity arrives in
   Phase 5/6, so `mss_require_swept_origin` defaults to `false` and the check is
   a no-op until then, rather than being silently ignored, **and**
5. The broken low was a *valid* structural low (formed by ≥ `mss.min_legs` = 2 swings).

Effect: bias flips to `BEARISH`; new `structural_high` = the high of the leg that
caused the shift. Bullish MSS is the mirror.

**Pending window.** A CHOCH without displacement stays pending for
`mss_confirm_window` bars (default 10); a later close through the *same* level
with displacement confirms the MSS then. Otherwise it expires and is counted.

**Precedence within a bar: MSS > CHOCH > BOS.** A bar that reverses the trend
does not also continue it, and a level is consumed once broken, so it cannot
produce a second event.

**Relationship (explicit):** every MSS is a CHOCH; not every CHOCH is an MSS.
A CHOCH without displacement stays a CHOCH and does not flip bias.
BOS is a separate class entirely (continuation). The three are stored as distinct
event types and are never conflated in features or in the setup taxonomy.

---

## 6. Displacement

Bar `i` (or a run of ≤ `disp.max_bars`, default 3, consecutive same-direction bars)
qualifies as displacement when the composite score ≥ threshold:

```
body_ratio     = |C - O| / ATR                     weight 0.40   (needs >= 1.0 to score full)
range_ratio    = (H - L) / ATR                     weight 0.20   (needs >= 1.5)
close_location = (C - L)/(H - L) bullish,          weight 0.20   (needs >= 0.70)
                 (H - C)/(H - L) bearish
imbalance      = 1 if the run leaves an FVG else 0 weight 0.20  (Phase 8)
displacement_score = weighted sum, 0..1
```

**Staged rollout.** Phase 4 ships the first three components with weights
0.50 / 0.25 / 0.25 (summing to 1.0) and `imbalance_weight = 0.0`, because FVGs
do not exist until Phase 8. Switching the fourth component on is a config change
that alters `rules_hash`, so "STRONG" never changes meaning silently. Multi-bar
runs (`disp.max_bars`) arrive with Phase 7.

Classes: `NONE < 0.35`, `WEAK 0.35–0.55`, `MODERATE 0.55–0.75`, `STRONG >= 0.75`.
All weights and cut-offs live in `smc_rules.displacement`. Volume is deliberately
excluded from the default score (tick volume is broker-dependent) but is stored as
a feature so its value can be measured later.

---

## 7. Liquidity

**Pools tracked:**

| type | definition |
|---|---|
| `SWING_HIGH_LIQ` / `SWING_LOW_LIQ` | price of any CONFIRMED swing, untouched since formation |
| `EQH` / `EQL` | ≥ 2 swing highs (lows) within `eq_tol_atr * ATR` (default 0.10) of each other, formed within `eq_max_gap` bars (default 50) |
| `PDH` / `PDL` | previous *completed* broker-day high/low |
| `PWH` / `PWL` | previous completed week high/low |
| `SESSION_HIGH/LOW` | high/low of the previous completed Asian / London / NY session |

Tolerances are always ATR- or percentage-based, never fixed pips — the same code
must behave sanely on `EURUSDm` (0.0001 tick) and `BTCUSDm` (1.0 tick).

Each pool carries: `price, kind, side (BUY_SIDE above price / SELL_SIDE below),
created_at_index, confirmed_at_index, touch_indices, swept_at_index,
consumed_at_index, strength`.

**Lifecycle** (all queried as-of a bar, never mutated in place):

- `INTACT` — price has not traded beyond the level since the pool formed.
- `SWEPT` — a wick went beyond it by more than
  `sweep_min_penetration_atr × ATR` (default 0.02) but the bar closed back on
  the origin side.
- `CONSUMED` — a bar **closed** beyond it. A wick that only occurs on the bar
  that closes through is not a sweep; it is the level being taken out.

**Confirmation timing.** A swing pool is known at the swing's
`confirmed_at_index`; an `EQH`/`EQL` pool when its **second** member confirms;
`PDH`/`PDL` at the first bar of the next day; a session pool at
`session.end_index + 1`. Nothing is ever known while the period that creates it
is still forming.

**Cluster growth.** A third equal high joins its cluster and raises the pool's
price and strength from its own confirmation bar onward. `price_at(t)` and
`member_count_at(t)` see only members confirmed by `t`.

**Strength** = `base(kind) + per_touch × min(touches, cap)`, plus
`per_extra_member × (members − 2)` scaled by `(0.5 + 0.5 × tightness)` for equal
levels. Bases: weekly 3.0 > daily 2.0 = equal 2.0 > session 1.5 > swing 1.0.
This ordering is a stated heuristic, not a measured result — Phase 13+ should
test whether it predicts anything and replace it if it does not.

---

## 8. Liquidity sweep

A **buy-side sweep** at bar `i` on pool `P` (side = BUY_SIDE) requires:
1. `P.status == INTACT` and `P.created_at < i`.
2. **Penetration:** `H_i > P.price + sweep.min_pen_atr * ATR_i` (default 0.02) —
   i.e. price genuinely traded through, not just touched.
3. **Rejection**, within `sweep.confirm_bars` (default 2) bars including `i`:
   `C_j < P.price` for some `j <= i + confirm_bars`, and
   close location of bar `i`: `(C_i - L_i)/(H_i - L_i) <= sweep.max_close_loc` (default 0.40).
4. Penetration is bounded: `H_i - P.price <= sweep.max_pen_atr * ATR_i` (default 1.5),
   above which it is classified `BREAKOUT`, not a sweep.

`confirmed_at_index` = the rejection bar, not the penetration bar.

Measured and stored: `sweep_magnitude_atr`, `rejection_size_atr`, `close_location`,
`bars_to_reject`, `tick_volume_ratio`, `distance_from_structure_atr`,
`pool_type`, `pool_strength`, `session`.

Sell-side sweep is the mirror.

---

## 9. Fair Value Gap (FVG)

Three-bar model on bars `(i-2, i-1, i)`:
- **Bullish FVG:** `L_i > H_{i-2}`. Gap = `[H_{i-2}, L_i]`.
- **Bearish FVG:** `H_i < L_{i-2}`. Gap = `[H_i, L_{i-2}]`.

Filters: `gap_size >= fvg.min_size_atr * ATR` (default 0.10); middle bar must be the
displacement bar when `fvg.require_displacement = true` (default true).

Lifecycle: `size, size_atr, mid (CE = consequent encroachment), fill_pct` (deepest
penetration so far), `status ∈ {FRESH, PARTIAL, MITIGATED(>= fvg.fill_mitigated, default 0.50 → CE), INVALIDATED(100%)}`,
`age_bars`, `touch_count`. `confirmed_at_index = i`.

---

## 10. Inverse FVG (IFVG)

An FVG that is fully traded through and then rejected becomes an IFVG of the
opposite polarity. Exact rule for a bullish FVG → bearish IFVG:
1. A bar closes fully below the gap low (`C_j < gap_low`) — full invalidation by close, not wick.
2. Within `ifvg.reclaim_bars` (default 10) bars, price returns into the original gap
   range and closes back below it (`H_k >= gap_low` and `C_k < gap_low`).

The IFVG's range is the original gap range; it is then tracked with the same
lifecycle fields as an FVG, with `origin_fvg_id` recorded. Mirror for bearish → bullish.

---

## 11. Order blocks

**Bullish OB:** the last down-close bar (`C < O`) at index `k` before a bullish
displacement leg (§6) that produced a BOS or MSS at index `i > k`, with
`i - k <= ob.max_lookback` (default 5) and no intervening up-close bar with a lower low.

Zone: `[L_k, H_k]` by default (`ob.zone = FULL_RANGE`); alternatives `BODY`
(`[min(O,C), max(O,C)]`) and `WICK_TO_BODY`. Bearish OB is the mirror (last up-close bar).

Validity requires **all**: displacement follows, BOS/MSS confirmed,
`(H_k - L_k) >= ob.min_size_atr * ATR` (default 0.20), and the OB is not already
mitigated at detection.

Lifecycle: `FRESH` (untouched) → `TOUCHED` → `MITIGATED` (price traded ≥ `ob.mit_pct`,
default 0.50, into the zone) → `INVALIDATED` (close beyond the far edge) →
`BREAKER` (see below). `freshness = touch_count == 0`.

**Breaker block:** an OB that gets invalidated by a close through it *and* whose
level is subsequently retested from the other side flips polarity — a failed
bullish OB becomes a bearish breaker. Tracked with `origin_ob_id`.

**Mitigation block:** an OB whose zone was entered but that produced a new
displacement in the original direction without full invalidation.

---

## 12. Dealing range & premium/discount

Dealing range = from the most recent confirmed structural low to the most recent
confirmed structural high **of the current leg** (i.e. the swing pair bracketing the
last BOS/MSS), requiring `range_width >= pd.min_range_atr * ATR` (default 2.0).

- 0–50 % of the range (from the low) = **DISCOUNT**
- 50 % ± `pd.eq_band` (default 0.03 → 47–53 %) = **EQUILIBRIUM**
- 50–100 % = **PREMIUM**

Optional OTE sub-zone 0.62–0.79 retracement, `pd.report_ote = true`.
Longs are preferred in discount, shorts in premium; the confluence engine scores
this rather than hard-blocking it.

---

## 13. Sessions (timezone configurable, defaults in UTC)

| session | default window (UTC) |
|---|---|
| Asian | 00:00 – 07:00 |
| London | 07:00 – 12:00 |
| NY AM (killzone) | 12:00 – 15:00 |
| NY PM | 15:00 – 20:00 |

Per session we track high, low, range, open and close, plus the bar indices of
the session's high and low. Windows are stored in a named tz
(`UTC`, `Europe/London`, `America/New_York`) and converted per bar, so a local
08:00 window shifts correctly in UTC when DST starts. A window whose end is at
or before its start wraps past midnight and remains **one** session instance.

A session is not complete until its last bar is in the past — a forming
session's high and low are still moving, so they cannot be liquidity. The
default windows deliberately leave 20:00–00:00 UTC uncovered.

---

## 14. Market regime (used as a similarity key, not a filter)

Computed on the HTF at each bar from closed data only:
- `volatility_regime`: ATR percentile over `regime.lookback` (default 500 bars) →
  `LOW < 33 %`, `NORMAL`, `HIGH > 66 %`.
- `trend_regime`: ADX(14) `< 20 → RANGE`, `20–30 → WEAK_TREND`, `> 30 → TREND`,
  signed by structure bias.
- `regime_key = f"{trend_regime}|{volatility_regime}"` — 9 buckets.

---

## 15. Setup taxonomy

A setup type is a canonical string built from the confirmed components, ordered:

```
{LIQUIDITY_EVENT}_{STRUCTURE_EVENT}_{POI}[_{PD}]
e.g. BUYSIDE_SWEEP_BEARISH_MSS_OB_FVG_PREMIUM
```

The v0.1 pre-registered setup families (fixed before any statistics are run):
1. `SWEEP_MSS_FVG` — sweep → MSS with displacement → entry on FVG retrace.
2. `SWEEP_MSS_OB` — same, entry on the order block.
3. `SWEEP_MSS_OB_FVG` — OB and FVG overlapping (strongest confluence).
4. `BOS_CONTINUATION_FVG` — HTF-aligned BOS → LTF FVG retrace.
5. `BREAKER_RETEST` — failed OB flipped to breaker, retested.

Everything else the detector sees is stored in the database as `OTHER_*` for later
analysis but is not eligible for a trade decision in v0.1.
