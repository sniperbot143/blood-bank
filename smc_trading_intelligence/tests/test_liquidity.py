"""Liquidity pools: inventory, lifecycle, strength and as-of queries."""

from __future__ import annotations

import pandas as pd
import pytest

from config.smc_rules import LiquidityConfig, SMCRules, SwingConfig
from liquidity.levels import PoolKind, PoolStatus, Side, build_liquidity
from tests.conftest import make_frame

GEOMETRY = SwingConfig(swing_left=2, swing_right=2, min_swing_atr=0.0)
# A short ATR period keeps fixtures small while still seeding ATR, which
# equal-level tolerances and pool strength both need.
RULES = SMCRules(atr_period=5, swing=GEOMETRY)


def _rules(**liquidity_kwargs) -> SMCRules:
    return SMCRules(atr_period=5, swing=GEOMETRY,
                    liquidity=LiquidityConfig(**liquidity_kwargs))


def _warmup(bars: int = 8, base: float = 100.0):
    """Small oscillating bars so ATR is seeded before the pattern starts."""
    return [base + (i % 2) + 0.5 for i in range(bars)], [base + (i % 2) - 0.5 for i in range(bars)]


def _hourly(highs, lows, start="2024-01-02 00:00", **kw):
    return make_frame(highs, lows, start=start, minutes=60, **kw)


# ---------------------------------------------------------------- swing pools

def _peak_frame():
    """A clear peak at index 5 and trough at index 11."""
    path = [100, 102, 104, 106, 108, 110, 108, 106, 104, 102, 100, 98,
            100, 102, 104, 106]
    return make_frame([p + 0.5 for p in path], [p - 0.5 for p in path])


def test_swing_highs_become_buy_side_pools():
    liquidity = build_liquidity(_peak_frame(), _rules(track_equal_levels=False,
                                                      track_daily=False, track_weekly=False,
                                                      track_sessions=False))
    highs = [p for p in liquidity.pools if p.kind is PoolKind.SWING_HIGH]
    lows = [p for p in liquidity.pools if p.kind is PoolKind.SWING_LOW]

    assert highs and lows
    assert all(p.side is Side.BUY_SIDE for p in highs)     # buy stops rest above highs
    assert all(p.side is Side.SELL_SIDE for p in lows)


def test_a_pool_is_not_known_before_its_swing_confirms():
    liquidity = build_liquidity(_peak_frame(), _rules(track_equal_levels=False,
                                                      track_daily=False, track_weekly=False,
                                                      track_sessions=False))
    pool = next(p for p in liquidity.pools if p.kind is PoolKind.SWING_HIGH)

    assert pool.confirmed_at_index > pool.created_at_index
    assert not pool.is_known_at(pool.confirmed_at_index - 1)
    assert pool.is_known_at(pool.confirmed_at_index)
    assert pool not in liquidity.known_at(pool.confirmed_at_index - 1)


# ------------------------------------------------------------------ lifecycle

def _lifecycle_frame():
    """A peak, then a wick above it that closes back under (SWEPT), then a
    close above it (CONSUMED). Returns (frame, peak_index)."""
    warm_highs, warm_lows = _warmup()
    highs = warm_highs + [102.5, 104.5, 106.5, 108.5, 110.5, 108.5, 106.5, 105.5]
    lows = warm_lows + [h - 2.0 for h in highs[len(warm_highs):]]
    peak_index = len(warm_highs) + 4          # the 110.5 bar

    highs += [111.0, 106.0, 105.0, 112.0, 113.0]
    lows += [105.0, 104.0, 103.0, 105.0, 106.0]

    closes = [(h + l) / 2 for h, l in zip(highs, lows)]
    closes[peak_index + 4] = 105.5            # the sweep bar rejects hard
    closes[peak_index + 7] = 111.8            # the close that takes the level out
    opens = [l + 0.2 for l in lows]
    return make_frame(highs, lows, opens=opens, closes=closes), peak_index


def test_pool_lifecycle_moves_intact_then_swept_then_consumed():
    frame, peak = _lifecycle_frame()
    liquidity = build_liquidity(frame, _rules(track_equal_levels=False, track_daily=False,
                                              track_weekly=False, track_sessions=False))
    pool = next(p for p in liquidity.pools
                if p.kind is PoolKind.SWING_HIGH and p.created_at_index == peak)

    assert pool.swept_at_index == peak + 4
    assert pool.consumed_at_index == peak + 7
    assert pool.status_at(peak + 3) is PoolStatus.INTACT
    assert pool.status_at(peak + 4) is PoolStatus.SWEPT
    assert pool.status_at(peak + 6) is PoolStatus.SWEPT
    assert pool.status_at(peak + 7) is PoolStatus.CONSUMED


def test_a_level_taken_out_by_a_close_is_consumed_not_swept():
    """A wick that only happens on the bar that closes through is not a sweep."""
    warm_highs, warm_lows = _warmup()
    highs = warm_highs + [102.5, 104.5, 106.5, 108.5, 110.5, 108.5, 106.5, 105.5, 112.0, 113.0]
    lows = warm_lows + [h - 2.0 for h in highs[len(warm_highs):]]
    peak = len(warm_highs) + 4
    closes = [h - 0.2 for h in highs]      # every bar closes near its high

    liquidity = build_liquidity(
        make_frame(highs, lows, closes=closes, opens=[l + 0.2 for l in lows]),
        _rules(track_equal_levels=False, track_daily=False, track_weekly=False,
               track_sessions=False),
    )
    pool = next(p for p in liquidity.pools
                if p.kind is PoolKind.SWING_HIGH and p.created_at_index == peak)

    assert pool.consumed_at_index is not None
    assert pool.swept_at_index is None


def test_intact_at_excludes_swept_and_consumed_pools():
    frame, peak = _lifecycle_frame()
    liquidity = build_liquidity(frame, _rules(track_equal_levels=False, track_daily=False,
                                              track_weekly=False, track_sessions=False))
    pool = next(p for p in liquidity.pools
                if p.kind is PoolKind.SWING_HIGH and p.created_at_index == peak)

    assert pool in liquidity.intact_at(peak + 3)
    assert pool not in liquidity.intact_at(peak + 4)
    assert pool in liquidity.known_at(peak + 4)      # known, just no longer intact


def test_touches_are_counted_up_to_consumption():
    frame, peak = _lifecycle_frame()
    liquidity = build_liquidity(frame, _rules(track_equal_levels=False, track_daily=False,
                                              track_weekly=False, track_sessions=False))
    pool = next(p for p in liquidity.pools
                if p.kind is PoolKind.SWING_HIGH and p.created_at_index == peak)

    assert pool.touch_count_at(peak + 3) == 0
    assert pool.touch_count_at(peak + 4) >= 1
    assert all(i <= pool.consumed_at_index for i in pool.touch_indices)


# ------------------------------------------------------------- equal levels

def _double_top_frame(second_top: float = 110.5):
    warm_highs, warm_lows = _warmup()
    highs = warm_highs + [102.5, 104.5, 106.5, 108.5, 110.5, 108.5, 106.5,
                          104.5, 106.5, 108.5, second_top, 108.5, 106.5, 104.5, 103.5]
    lows = warm_lows + [h - 1.0 for h in highs[len(warm_highs):]]
    return make_frame(highs, lows)


def test_two_equal_highs_form_an_eqh_pool():
    liquidity = build_liquidity(_double_top_frame(),
                                _rules(track_daily=False, track_weekly=False,
                                       track_sessions=False))
    eqh = [p for p in liquidity.pools if p.kind is PoolKind.EQH]

    assert len(eqh) == 1
    assert eqh[0].side is Side.BUY_SIDE
    assert eqh[0].member_count_at(liquidity.n_bars - 1) == 2


def test_highs_outside_the_tolerance_do_not_pair_up():
    liquidity = build_liquidity(_double_top_frame(second_top=118.0),
                                _rules(track_daily=False, track_weekly=False,
                                       track_sessions=False))
    assert not [p for p in liquidity.pools if p.kind is PoolKind.EQH]


def test_a_wider_tolerance_pairs_them():
    liquidity = build_liquidity(_double_top_frame(second_top=112.5),
                                _rules(equal_tolerance_atr=3.0, track_daily=False,
                                       track_weekly=False, track_sessions=False))
    assert [p for p in liquidity.pools if p.kind is PoolKind.EQH]


def test_highs_too_far_apart_in_time_do_not_pair_up():
    liquidity = build_liquidity(_double_top_frame(),
                                _rules(equal_max_gap_bars=2, track_daily=False,
                                       track_weekly=False, track_sessions=False))
    assert not [p for p in liquidity.pools if p.kind is PoolKind.EQH]


def test_an_eqh_pool_is_not_known_before_its_second_member_confirms():
    liquidity = build_liquidity(_double_top_frame(),
                                _rules(track_daily=False, track_weekly=False,
                                       track_sessions=False))
    pool = next(p for p in liquidity.pools if p.kind is PoolKind.EQH)

    assert pool.confirmed_at_index > pool.created_at_index
    assert not pool.is_known_at(pool.confirmed_at_index - 1)


# ------------------------------------------------------- calendar & sessions

def _three_days():
    """Sat 6 Jan -> Mon 8 Jan 2024, so the frame crosses a week boundary too."""
    hours = 72
    highs = [100 + (i % 24) * 0.5 + 0.5 for i in range(hours)]
    lows = [100 + (i % 24) * 0.5 - 0.5 for i in range(hours)]
    return _hourly(highs, lows, start="2024-01-06 00:00")


def test_previous_day_levels_appear_at_the_start_of_the_next_day():
    frame = _three_days()
    liquidity = build_liquidity(frame, _rules(track_swing_pools=False,
                                              track_equal_levels=False,
                                              track_weekly=False, track_sessions=False))
    pdh = [p for p in liquidity.pools if p.kind is PoolKind.PDH]

    assert len(pdh) == 2                       # three days -> two completed days
    first = pdh[0]
    assert first.confirmed_at_index == 24      # first bar of day two
    assert first.price == pytest.approx(frame["high"].iloc[:24].max())
    assert not first.is_known_at(23)           # the day was still forming


def test_previous_day_low_mirrors_it():
    frame = _three_days()
    liquidity = build_liquidity(frame, _rules(track_swing_pools=False,
                                              track_equal_levels=False,
                                              track_weekly=False, track_sessions=False))
    pdl = next(p for p in liquidity.pools if p.kind is PoolKind.PDL)
    assert pdl.side is Side.SELL_SIDE
    assert pdl.price == pytest.approx(frame["low"].iloc[:24].min())


def test_a_broker_day_can_start_at_a_different_hour():
    frame = _three_days()
    default = build_liquidity(frame, _rules(track_swing_pools=False, track_equal_levels=False,
                                            track_weekly=False, track_sessions=False))
    shifted = build_liquidity(frame, _rules(day_start_hour=22, track_swing_pools=False,
                                            track_equal_levels=False, track_weekly=False,
                                            track_sessions=False))
    default_pdh = next(p for p in default.pools if p.kind is PoolKind.PDH)
    shifted_pdh = next(p for p in shifted.pools if p.kind is PoolKind.PDH)
    assert default_pdh.confirmed_at_index != shifted_pdh.confirmed_at_index


def test_session_levels_appear_only_after_the_session_closes():
    frame = _three_days()
    liquidity = build_liquidity(frame, _rules(track_swing_pools=False, track_equal_levels=False,
                                              track_daily=False, track_weekly=False))
    session_pools = [p for p in liquidity.pools if p.kind is PoolKind.SESSION_HIGH]

    assert session_pools
    instance = liquidity.sessions.instances[0]
    matching = next(p for p in session_pools if p.origin == instance.label)
    assert matching.confirmed_at_index == instance.end_index + 1
    assert matching.price == pytest.approx(instance.high)


# ------------------------------------------------------------------ strength

def test_equal_levels_are_stronger_than_a_lone_swing():
    frame = _double_top_frame()
    liquidity = build_liquidity(frame, _rules(track_daily=False, track_weekly=False,
                                              track_sessions=False))
    at = liquidity.n_bars - 1
    eqh = next(p for p in liquidity.pools if p.kind is PoolKind.EQH)
    swing = next(p for p in liquidity.pools if p.kind is PoolKind.SWING_HIGH)
    assert eqh.strength_at(at) > swing.strength_at(at)


def test_weekly_levels_outrank_daily_ones():
    frame = _three_days()
    liquidity = build_liquidity(frame, _rules(track_swing_pools=False, track_equal_levels=False,
                                              track_sessions=False))
    at = liquidity.n_bars - 1
    daily = [p for p in liquidity.pools if p.kind is PoolKind.PDH]
    weekly = [p for p in liquidity.pools if p.kind is PoolKind.PWH]
    assert weekly, "fixture must cross a week boundary"
    assert weekly[0].strength_at(at) > daily[0].strength_at(at)


def test_retests_increase_strength():
    frame, peak = _lifecycle_frame()
    liquidity = build_liquidity(frame, _rules(track_equal_levels=False, track_daily=False,
                                              track_weekly=False, track_sessions=False))
    pool = next(p for p in liquidity.pools
                if p.kind is PoolKind.SWING_HIGH and p.created_at_index == peak)
    assert pool.strength_at(peak + 4) > pool.strength_at(peak + 3)


# --------------------------------------------------------------- map queries

def test_nearest_pools_are_found_on_the_correct_side():
    frame = _peak_frame()
    liquidity = build_liquidity(frame, _rules(track_daily=False, track_weekly=False,
                                              track_sessions=False))
    at = liquidity.n_bars - 1
    price = float(frame["close"].iloc[-1])

    above = liquidity.nearest_above(price, at)
    below = liquidity.nearest_below(price, at)

    if above:
        assert above.side is Side.BUY_SIDE and above.price_at(at) > price
    if below:
        assert below.side is Side.SELL_SIDE and below.price_at(at) < price


def test_above_and_below_are_sorted_by_proximity():
    frame = _peak_frame()
    liquidity = build_liquidity(frame, _rules(track_daily=False, track_weekly=False,
                                              track_sessions=False))
    at = liquidity.n_bars - 1
    price = float(frame["close"].iloc[-1])

    above = [p.price_at(at) for p in liquidity.above(price, at)]
    below = [p.price_at(at) for p in liquidity.below(price, at)]
    assert above == sorted(above)
    assert below == sorted(below, reverse=True)


def test_pool_types_can_be_switched_off():
    frame = _three_days()
    liquidity = build_liquidity(frame, _rules(track_swing_pools=False, track_equal_levels=False,
                                              track_daily=False, track_weekly=False,
                                              track_sessions=False))
    assert liquidity.pools == []


def test_counts_and_frame_output():
    frame = _three_days()
    liquidity = build_liquidity(frame, RULES)
    at = liquidity.n_bars - 1

    assert sum(liquidity.counts(at).values()) == len(liquidity.known_at(at))
    assert sum(liquidity.status_counts(at).values()) == len(liquidity.known_at(at))
    out = liquidity.to_frame(at)
    assert isinstance(out, pd.DataFrame) and "strength" in out.columns


def test_empty_frame_is_handled():
    liquidity = build_liquidity(make_frame([], []), RULES)
    assert liquidity.pools == []
    assert liquidity.known_at(0) == []
