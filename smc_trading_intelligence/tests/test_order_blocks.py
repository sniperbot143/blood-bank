"""Order blocks, their lifecycle, and premium/discount context."""

from __future__ import annotations

import pytest

from config.smc_rules import (
    OBZone,
    OrderBlockConfig,
    RangeConfig,
    SMCRules,
    StructureConfig,
    SwingConfig,
)
from context.premium_discount import Zone, dealing_range_at, dealing_range_series, zone_share
from orderblocks.order_blocks import OBDirection, OBStatus, detect_order_blocks
from structure.market_structure import build_structure
from tests.conftest import make_frame

RULES = SMCRules(
    atr_period=5,
    swing=SwingConfig(swing_left=2, swing_right=2, min_swing_atr=0.0),
    structure=StructureConfig(range_atr_mult=0.0, track_internal=False),
    order_blocks=OrderBlockConfig(min_size_atr=0.0, min_displacement=0.0),
)


def _rally_frame(pullback_low: float | None = None):
    """Warm-up, a down candle, then a displaced rally that breaks structure."""
    highs = [100.5, 101.5, 100.5, 101.5, 100.5, 101.5, 100.5, 101.5]
    lows = [99.5, 100.5, 99.5, 100.5, 99.5, 100.5, 99.5, 100.5]
    opens = [(h + l) / 2 for h, l in zip(highs, lows)]
    closes = list(opens)

    # a swing high to break, then a pullback low
    for h, l, o, c in ((103.0, 101.0, 101.5, 102.5), (102.0, 100.0, 101.8, 100.5),
                       (101.5, 99.5, 101.0, 100.0)):
        highs.append(h); lows.append(l); opens.append(o); closes.append(c)

    # the order block: a down-close bar right before the rally
    highs.append(101.0); lows.append(99.0); opens.append(100.8); closes.append(99.2)

    # displaced rally that closes above the swing high -> BOS
    for h, l, o, c in ((104.0, 99.5, 99.5, 103.8), (107.0, 103.5, 103.8, 106.8)):
        highs.append(h); lows.append(l); opens.append(o); closes.append(c)

    # what happens next is the test parameter
    for k in range(6):
        low = pullback_low if (pullback_low is not None and k == 1) else 106.0
        highs.append(107.5); lows.append(low)
        opens.append(106.5); closes.append(max(low, 106.5))
    return make_frame(highs, lows, opens=opens, closes=closes)


def _blocks(frame, rules: SMCRules = RULES):
    return detect_order_blocks(frame, None, rules)


def test_a_bullish_order_block_is_the_last_down_candle_before_the_leg():
    blocks = _blocks(_rally_frame())
    bullish = [b for b in blocks.blocks if b.direction is OBDirection.BULLISH]

    assert bullish
    block = bullish[0]
    assert block.origin_index == 11              # the down-close bar
    assert block.top == pytest.approx(101.0)
    assert block.bottom == pytest.approx(99.0)


def test_an_order_block_is_known_only_at_its_break_bar():
    blocks = _blocks(_rally_frame())
    block = next(b for b in blocks.blocks if b.direction is OBDirection.BULLISH)

    assert block.confirmed_at_index > block.origin_index
    assert not block.is_known_at(block.confirmed_at_index - 1)
    assert block.is_known_at(block.confirmed_at_index)


def test_an_untouched_block_stays_fresh():
    blocks = _blocks(_rally_frame())
    block = next(b for b in blocks.blocks if b.direction is OBDirection.BULLISH)
    assert block.status_at(blocks.n_bars - 1) is OBStatus.FRESH


def test_a_retrace_into_the_zone_marks_it_touched_then_mitigated():
    partial = _blocks(_rally_frame(pullback_low=100.8))
    deep = _blocks(_rally_frame(pullback_low=99.5))
    last = partial.n_bars - 1

    shallow_block = next(b for b in partial.blocks if b.direction is OBDirection.BULLISH)
    deep_block = next(b for b in deep.blocks if b.direction is OBDirection.BULLISH)

    assert shallow_block.status_at(last) is OBStatus.TOUCHED
    assert deep_block.status_at(last) in (OBStatus.MITIGATED, OBStatus.INVALIDATED)
    assert deep_block.fill_at(last) > shallow_block.fill_at(last)


def test_fill_never_decreases():
    blocks = _blocks(_rally_frame(pullback_low=100.0))
    block = next(b for b in blocks.blocks if b.direction is OBDirection.BULLISH)
    fills = [block.fill_at(t) for t in range(blocks.n_bars)]
    assert fills == sorted(fills)


def test_zone_mode_changes_the_block_geometry():
    frame = _rally_frame()
    full = _blocks(frame)
    body_rules = SMCRules(atr_period=5, swing=RULES.swing, structure=RULES.structure,
                          order_blocks=OrderBlockConfig(min_size_atr=0.0, min_displacement=0.0,
                                                        zone=OBZone.BODY))
    body = _blocks(frame, body_rules)

    full_block = next(b for b in full.blocks if b.direction is OBDirection.BULLISH)
    body_block = next(b for b in body.blocks if b.direction is OBDirection.BULLISH)
    assert body_block.size < full_block.size


def test_small_blocks_are_rejected():
    demanding = SMCRules(atr_period=5, swing=RULES.swing, structure=RULES.structure,
                         order_blocks=OrderBlockConfig(min_size_atr=9.0, min_displacement=0.0))
    blocks = _blocks(_rally_frame(), demanding)
    assert not blocks.blocks
    assert blocks.rejected.get("TOO_SMALL", 0) > 0


def test_displacement_is_required_of_the_leg():
    demanding = SMCRules(atr_period=5, swing=RULES.swing, structure=RULES.structure,
                         order_blocks=OrderBlockConfig(min_size_atr=0.0, min_displacement=0.99))
    blocks = _blocks(_rally_frame(), demanding)
    assert not blocks.blocks
    assert blocks.rejected.get("NO_DISPLACEMENT", 0) > 0


def test_blocks_never_repaint():
    frame = _rally_frame(pullback_low=100.0)
    full = _blocks(frame)

    for t in range(0, len(frame), 2):
        live = _blocks(frame.iloc[: t + 1])
        seen = [(b.direction.value, b.origin_index, round(b.top, 6), b.status_at(t).value)
                for b in live.known_at(t)]
        expected = [(b.direction.value, b.origin_index, round(b.top, 6), b.status_at(t).value)
                    for b in full.known_at(t)]
        assert seen == expected


def test_a_failed_block_can_flip_to_a_breaker():
    """Invalidated by a CLOSE through it, then retested from the other side.

    A wick through is only mitigation; the block dies on a close.
    """
    frame = _rally_frame()
    # Overwrite the tail: collapse through the block, then retest it from below.
    tail = [(101.0, 97.0, 100.5, 97.5),      # closes below the 99.0 bottom
            (98.5, 96.5, 97.5, 97.0),
            (99.5, 97.0, 97.2, 97.5),        # trades back INTO the zone...
            (99.2, 96.0, 97.5, 96.5)]        # ...and closes below it again
    frame = frame.copy()
    for offset, (high, low, open_, close) in enumerate(tail):
        row = len(frame) - len(tail) + offset
        frame.iloc[row, frame.columns.get_loc("high")] = high
        frame.iloc[row, frame.columns.get_loc("low")] = low
        frame.iloc[row, frame.columns.get_loc("open")] = open_
        frame.iloc[row, frame.columns.get_loc("close")] = close

    blocks = _blocks(frame)
    block = next(b for b in blocks.blocks if b.direction is OBDirection.BULLISH)

    assert block.invalidated_at_index is not None
    assert block.breaker_direction() is OBDirection.BEARISH
    if block.breaker_at_index is not None:
        assert block.status_at(block.breaker_at_index) is OBStatus.BREAKER


def test_series_helpers():
    blocks = _blocks(_rally_frame())
    last = blocks.n_bars - 1
    assert sum(blocks.counts(last).values()) == len(blocks.known_at(last))
    assert blocks.nearest(100.0, last) is not None
    assert "direction" in blocks.to_frame(last).columns


def test_empty_frame_is_handled():
    assert _blocks(make_frame([], [])).blocks == []


# ------------------------------------------------------- premium / discount

def _range_rules(**kwargs) -> SMCRules:
    return SMCRules(atr_period=5, swing=RULES.swing, structure=RULES.structure,
                    dealing_range=RangeConfig(**kwargs))


def _range_frame():
    path = [100, 102, 104, 106, 108, 110, 108, 106, 104, 102, 100,
            102, 104, 106, 108, 106, 104]
    return make_frame([p + 0.5 for p in path], [p - 0.5 for p in path])


def test_price_near_the_low_is_discount_and_near_the_high_is_premium():
    frame = _range_frame()
    rules = _range_rules(min_range_atr=0.0)
    structure = build_structure(frame, rules)
    last = len(frame) - 1

    low_price = dealing_range_at(frame, structure, last, rules, price=structure.current.structural_low.price + 0.1)
    high_price = dealing_range_at(frame, structure, last, rules, price=structure.current.structural_high.price - 0.1)

    assert low_price.zone is Zone.DISCOUNT
    assert high_price.zone is Zone.PREMIUM
    assert low_price.position < high_price.position


def test_the_midpoint_is_equilibrium():
    frame = _range_frame()
    rules = _range_rules(min_range_atr=0.0, equilibrium_band=0.05)
    structure = build_structure(frame, rules)
    last = len(frame) - 1
    state = structure.current

    mid = (state.structural_high.price + state.structural_low.price) / 2
    assert dealing_range_at(frame, structure, last, rules, price=mid).zone is Zone.EQUILIBRIUM


def test_a_range_too_narrow_to_trade_reports_no_range():
    frame = _range_frame()
    rules = _range_rules(min_range_atr=50.0)
    structure = build_structure(frame, rules)
    result = dealing_range_at(frame, structure, len(frame) - 1, rules)

    assert result.zone is Zone.NO_RANGE
    assert not result.is_valid


def test_favours_matches_the_trade_direction():
    frame = _range_frame()
    rules = _range_rules(min_range_atr=0.0)
    structure = build_structure(frame, rules)
    last = len(frame) - 1

    discount = dealing_range_at(frame, structure, last, rules,
                                price=structure.current.structural_low.price + 0.1)
    assert discount.favours(bullish=True)
    assert not discount.favours(bullish=False)


def test_the_series_matches_the_per_bar_call():
    frame = _range_frame()
    rules = _range_rules(min_range_atr=0.0)
    structure = build_structure(frame, rules)
    series = dealing_range_series(frame, structure, rules)

    assert len(series) == len(frame)
    for t in (5, 10, len(frame) - 1):
        direct = dealing_range_at(frame, structure, t, rules)
        assert series[t].zone is direct.zone
        if direct.is_valid:
            assert series[t].position == pytest.approx(direct.position)


def test_zone_share_sums_to_one():
    frame = _range_frame()
    rules = _range_rules(min_range_atr=0.0)
    shares = zone_share(dealing_range_series(frame, build_structure(frame, rules), rules))
    assert sum(shares.values()) == pytest.approx(1.0)


def test_ote_is_reported_inside_the_retracement_band():
    frame = _range_frame()
    rules = _range_rules(min_range_atr=0.0)
    structure = build_structure(frame, rules)
    state = structure.current
    span = state.structural_high.price - state.structural_low.price
    ote_price = state.structural_high.price - 0.7 * span      # a 70% retracement

    result = dealing_range_at(frame, structure, len(frame) - 1, rules, price=ote_price)
    assert result.in_ote
