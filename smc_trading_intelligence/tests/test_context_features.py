"""Multi-timeframe alignment, regime labelling, context and features."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from config.smc_rules import MTFConfig, RegimeConfig, SMCRules, StructureConfig, SwingConfig
from context.market_regime import TrendRegime, VolatilityRegime, build_regimes
from context.mtf_bias import align_htf, build_mtf, resample_frame
from features.context import MarketContext
from features.feature_engineering import extract_features
from structure.market_structure import build_structure
from tests.conftest import make_frame

RULES = SMCRules(
    swing=SwingConfig(swing_left=2, swing_right=2, min_swing_atr=0.0),
    structure=StructureConfig(track_internal=False),
    mtf=MTFConfig(higher_timeframes=["M15"]),
)


def _walk(n: int = 300, seed: int = 71, start: str = "2024-01-02 00:00", minutes: int = 5):
    rng = np.random.default_rng(seed)
    closes = 2000 + rng.normal(0, 1.2, n).cumsum()
    wick = np.abs(rng.normal(0.5, 0.25, n))
    return make_frame(list(closes + wick), list(closes - wick),
                      opens=list(closes - wick * 0.2), closes=list(closes),
                      start=start, minutes=minutes)


# ------------------------------------------------------------------- MTF

def test_resampling_aggregates_ohlc_correctly():
    frame = _walk(60)
    m15 = resample_frame(frame, "M15")

    assert len(m15) == 20
    first = frame.iloc[:3]
    assert m15["open"].iloc[0] == pytest.approx(first["open"].iloc[0])
    assert m15["high"].iloc[0] == pytest.approx(first["high"].max())
    assert m15["low"].iloc[0] == pytest.approx(first["low"].min())
    assert m15["close"].iloc[0] == pytest.approx(first["close"].iloc[-1])


def test_an_incomplete_final_bucket_is_dropped():
    """A forming HTF bar must never appear -- that is the whole leak risk."""
    frame = _walk(61)               # 20 full M15 bars + 1 stray M5 bar
    assert len(resample_frame(frame, "M15")) == 20


def test_alignment_only_exposes_closed_htf_bars():
    frame = _walk(60)
    m15 = resample_frame(frame, "M15")
    mapping = align_htf(frame.index, "M5", m15.index, "M15")

    # The first M15 bar covers M5 bars 0-2 and closes when bar 2 closes.
    assert mapping[0] == -1
    assert mapping[1] == -1
    assert mapping[2] == 0          # usable from the bar on which it closes
    assert mapping[3] == 0
    assert mapping[5] == 1


def test_the_aligned_htf_bar_never_ends_after_the_ltf_bar():
    frame = _walk(120)
    m15 = resample_frame(frame, "M15")
    mapping = align_htf(frame.index, "M5", m15.index, "M15")

    for t, htf in enumerate(mapping):
        if htf < 0:
            continue
        htf_close = m15.index[htf] + pd.Timedelta(minutes=15)
        ltf_close = frame.index[t] + pd.Timedelta(minutes=5)
        assert htf_close <= ltf_close


def test_mtf_context_reports_agreement_and_conflict():
    frame = _walk(300)
    mtf = build_mtf(frame, ["M15"], RULES)
    last = len(frame) - 1

    summary = mtf.summary_at(last)
    assert set(summary) == {"M15"}
    bullish_ok = mtf.aligned(last, direction_bullish=True)
    bearish_ok = mtf.aligned(last, direction_bullish=False)
    assert not (bullish_ok and bearish_ok)      # cannot agree with both


def test_htf_bias_is_reproducible_under_truncation():
    frame = _walk(300)
    full = build_mtf(frame, ["M15"], RULES)

    for t in (100, 180, 250, 299):
        live = build_mtf(frame.iloc[: t + 1], ["M15"], RULES)
        assert live.bias_at("M15", t) is full.bias_at("M15", t)


# ---------------------------------------------------------------- regime

def test_regimes_are_labelled_and_shares_sum_to_one():
    frame = _walk(400)
    structure = build_structure(frame, RULES, with_breaks=True)
    regimes = build_regimes(frame, structure, RULES)

    assert len(regimes.regimes) == len(frame)
    assert sum(regimes.share().values()) == pytest.approx(1.0)
    assert "|" in regimes.key_at(len(frame) - 1)


def test_regime_is_unknown_before_its_inputs_are_seeded():
    frame = _walk(60)
    regimes = build_regimes(frame, None, RULES)
    assert regimes.at(0).volatility is VolatilityRegime.UNKNOWN
    assert regimes.at(0).trend is TrendRegime.UNKNOWN


def test_regime_config_validates_its_ordering():
    with pytest.raises(ValueError):
        RegimeConfig(low_vol_percentile=0.9, high_vol_percentile=0.1)
    with pytest.raises(ValueError):
        RegimeConfig(range_adx=50.0, trend_adx=10.0)


# --------------------------------------------------------------- context

def test_context_builds_every_layer():
    context = MarketContext.build(_walk(400), RULES)

    assert context.n_bars == 400
    assert context.swings.swings
    assert context.liquidity.pools
    assert len(context.ranges) == 400
    assert len(context.regimes.regimes) == 400
    assert "M15" in context.mtf.views


def test_a_snapshot_contains_nothing_from_the_future():
    context = MarketContext.build(_walk(400), RULES)
    t = 250
    snap = context.at(t)

    assert snap.index == t
    assert all(f.confirmed_at_index <= t for f in snap.active_fvgs)
    assert all(b.confirmed_at_index <= t for b in snap.tradeable_obs)
    assert all(p.confirmed_at_index <= t for p in snap.intact_pools)
    if snap.last_break is not None:
        assert snap.last_break.index <= t
    if snap.last_sweep is not None:
        assert snap.last_sweep.confirmed_at_index <= t


def test_building_on_a_truncated_frame_gives_the_same_snapshot():
    """The point of building once: `at(t)` == a fresh build over frame[:t+1]."""
    frame = _walk(300)
    full = MarketContext.build(frame, RULES)

    for t in (150, 220, 299):
        live = MarketContext.build(frame.iloc[: t + 1], RULES)
        a, b = full.at(t), live.at(t)
        assert a.bias is b.bias
        assert a.regime.key == b.regime.key
        assert a.dealing_range.zone is b.dealing_range.zone
        assert len(a.active_fvgs) == len(b.active_fvgs)
        assert len(a.tradeable_obs) == len(b.tradeable_obs)
        assert len(a.intact_pools) == len(b.intact_pools)


def test_describe_is_readable():
    context = MarketContext.build(_walk(300), RULES)
    text = context.describe(250)
    assert "bias" in text and "regime" in text


def test_out_of_range_snapshot_raises():
    context = MarketContext.build(_walk(100), RULES)
    with pytest.raises(IndexError):
        context.at(500)


# -------------------------------------------------------------- features

def test_features_cover_every_documented_group():
    context = MarketContext.build(_walk(400), RULES)
    features = extract_features(context, 300, direction_bullish=True)
    values = features.values

    for key in ("bias", "regime_key", "session", "pd_zone", "structure_event",
                "liquidity_event", "poi_type", "htf_bias_agreement",
                "confluence_count", "direction"):
        assert key in values


def test_missing_objects_are_nan_not_zero():
    """A zero would read as 'a zero-sized block existed'. It must be NaN."""
    context = MarketContext.build(_walk(400), RULES)
    features = extract_features(context, 40, direction_bullish=True)

    if not features.values["ob_present"]:
        assert math.isnan(features.values["ob_size_atr"])
    if not features.values["fvg_present"]:
        assert math.isnan(features.values["fvg_size_atr"])


def test_direction_flips_the_alignment_features():
    context = MarketContext.build(_walk(400), RULES)
    long_side = extract_features(context, 300, direction_bullish=True)
    short_side = extract_features(context, 300, direction_bullish=False)

    assert long_side.values["direction"] == "BUY"
    assert short_side.values["direction"] == "SELL"
    assert long_side.values["structure_aligned"] != short_side.values["structure_aligned"] or \
        long_side.values["structure_event"] == "NONE"


def test_trade_geometry_features_are_computed_when_supplied():
    context = MarketContext.build(_walk(400), RULES)
    snap = context.at(300)
    features = extract_features(
        context, 300, direction_bullish=True, snapshot=snap,
        entry=snap.close, stop_loss=snap.close - 2.0, take_profit=snap.close + 6.0,
    )
    assert features.values["risk_reward"] == pytest.approx(3.0)
    assert features.values["risk_atr"] > 0


def test_categorical_and_numeric_views():
    context = MarketContext.build(_walk(400), RULES)
    features = extract_features(context, 300, direction_bullish=True)

    assert set(features.categorical()) >= {"direction", "regime_key", "session"}
    assert all(isinstance(v, float) for v in features.numeric().values())


def test_features_are_reproducible_under_truncation():
    frame = _walk(300)
    full = MarketContext.build(frame, RULES)
    live = MarketContext.build(frame.iloc[:251], RULES)

    a = extract_features(full, 250, direction_bullish=True).values
    b = extract_features(live, 250, direction_bullish=True).values

    for key in ("bias", "regime_key", "pd_zone", "structure_event", "liquidity_event",
                "poi_type", "confluence_count"):
        assert a[key] == b[key], key
