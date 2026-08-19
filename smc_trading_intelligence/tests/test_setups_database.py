"""Setups, trade geometry, outcome labelling and the setup census."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtesting.labeling import Outcome, label_all, label_outcome, outcome_counts
from config.smc_rules import SMCRules, SetupConfig, StructureConfig, SwingConfig
from database.models import SetupStore
from features.context import MarketContext
from risk.levels import build_levels, position_size
from signals.setups import SetupFamily, detect_setups
from tests.conftest import make_frame, make_market_frame

RULES = SMCRules(
    swing=SwingConfig(swing_left=2, swing_right=2, min_swing_atr=0.5),
    structure=StructureConfig(track_internal=False),
    setups=SetupConfig(max_hold_bars=40),
)


def _walk(n: int = 900, seed: int = 11):
    return make_market_frame(n, seed)


@pytest.fixture(scope="module")
def context():
    return MarketContext.build(_walk(), RULES)


# ------------------------------------------------------------ trade levels

def test_levels_place_the_stop_beyond_the_protective_price(context):
    levels = build_levels(context, 300, bullish=True, entry=context.at(300).close,
                          protective_price=context.at(300).close - 3.0,
                          stop_source="TEST", config=RULES.setups)
    assert levels.is_valid
    assert levels.stop_loss < context.at(300).close - 3.0     # buffer applied
    assert levels.take_profit_1 > levels.entry
    assert levels.rr1 >= RULES.setups.min_rr


def test_a_stop_on_the_wrong_side_is_refused(context):
    levels = build_levels(context, 300, bullish=True, entry=context.at(300).close,
                          protective_price=context.at(300).close + 5.0,
                          stop_source="TEST", config=RULES.setups)
    assert not levels.is_valid
    assert levels.invalid_reason == "STOP_ON_WRONG_SIDE"


def test_an_absurdly_wide_stop_is_refused(context):
    snap = context.at(300)
    levels = build_levels(context, 300, bullish=True, entry=snap.close,
                          protective_price=snap.close - 100 * snap.atr,
                          stop_source="TEST", config=RULES.setups)
    assert levels.invalid_reason == "STOP_TOO_WIDE"


def test_position_size_rounds_down_never_up():
    lots = position_size(10_000, 1.0, risk_price=10.0, contract_size=1.0,
                         volume_step=0.01, volume_min=0.01)
    assert lots == pytest.approx(10.0)
    assert position_size(100, 1.0, risk_price=1000.0) == 0.0     # below the minimum


# ------------------------------------------------------------------ setups

def test_setups_are_detected_from_the_registered_families(context):
    setups = detect_setups(context, RULES)
    assert setups.candidates
    families = {c.family for c in setups.candidates}
    assert families <= set(SetupFamily)


def test_every_candidate_has_valid_geometry(context):
    for candidate in detect_setups(context, RULES).candidates:
        levels = candidate.levels
        assert levels.is_valid
        if candidate.bullish:
            assert levels.stop_loss < levels.entry < levels.take_profit_1
        else:
            assert levels.take_profit_1 < levels.entry < levels.stop_loss


def test_a_candidate_uses_nothing_after_its_signal_bar(context):
    frame = context.frame
    setups = detect_setups(context, RULES)
    if not setups.candidates:
        pytest.skip("no candidates in this fixture")

    candidate = setups.candidates[len(setups.candidates) // 2]
    t = candidate.signal_index
    live_context = MarketContext.build(frame.iloc[: t + 1], RULES)
    live = detect_setups(live_context, RULES)

    matching = [c for c in live.candidates if c.signal_index == t]
    assert matching, "the setup should be issuable at its own signal bar"
    assert matching[-1].family is candidate.family
    assert matching[-1].levels.entry == pytest.approx(candidate.levels.entry)


def test_overlapping_setups_are_flagged_not_deleted(context):
    setups = detect_setups(context, RULES)
    superseded = [c for c in setups.candidates if c.superseded]
    tradeable = setups.tradeable()

    assert len(tradeable) + len(superseded) == len(setups.candidates)
    for candidate in superseded:
        assert candidate.notes                      # says why


def test_rejections_are_counted(context):
    setups = detect_setups(context, RULES)
    assert isinstance(setups.rejected, dict)


# ---------------------------------------------------------------- outcomes

def test_outcomes_cover_every_candidate(context):
    setups = detect_setups(context, RULES)
    outcomes = label_all(context.frame, setups.candidates, RULES.setups)

    assert len(outcomes) == len(setups.candidates)
    assert sum(outcome_counts(outcomes).values()) == len(outcomes)


def test_losers_are_kept_not_discarded(context):
    outcomes = label_all(context.frame, detect_setups(context, RULES).candidates,
                         RULES.setups)
    losses = [o for o in outcomes if o.outcome is Outcome.SL_FIRST]
    assert losses, "a database of winners only would make every probability a lie"
    assert all(o.r_multiple == -1.0 for o in losses)


def test_an_unfilled_limit_is_labelled_no_fill(context):
    setups = detect_setups(context, RULES)
    impatient = SetupConfig(entry_valid_bars=1, max_hold_bars=40)
    outcomes = label_all(context.frame, setups.candidates, impatient)
    patient = label_all(context.frame, setups.candidates, RULES.setups)

    no_fill_impatient = sum(1 for o in outcomes if o.outcome is Outcome.NO_FILL)
    no_fill_patient = sum(1 for o in patient if o.outcome is Outcome.NO_FILL)
    assert no_fill_impatient >= no_fill_patient


def test_intrabar_ambiguity_resolves_against_the_trade():
    """One bar covering both TP and SL must label SL_FIRST, and say so."""
    frame = make_frame([100.5, 101.0, 108.0, 101.0], [99.5, 99.0, 90.0, 99.0],
                       opens=[100.0, 100.0, 100.0, 100.0],
                       closes=[100.0, 100.0, 100.0, 100.0])

    class _Levels:
        entry, stop_loss = 100.0, 95.0
        take_profit_1 = take_profit_2 = take_profit_3 = 105.0
        is_valid = True

    class _Candidate:
        bullish, signal_index = True, 0
        levels = _Levels()
        features = type("F", (), {"get": staticmethod(lambda *_a, **_k: 1.0)})()

    result = label_outcome(frame, _Candidate(), SetupConfig(spread_cost_atr=0.0,
                                                            slippage_atr=0.0))
    assert result.outcome is Outcome.SL_FIRST
    assert result.ambiguous


def test_costs_reduce_the_recorded_r(context):
    setups = detect_setups(context, RULES)
    if not setups.candidates:
        pytest.skip("no candidates")

    free = SetupConfig(spread_cost_atr=0.0, slippage_atr=0.0, max_hold_bars=40)
    dear = SetupConfig(spread_cost_atr=0.5, slippage_atr=0.5, max_hold_bars=40)
    free_r = [o.r_multiple for o in label_all(context.frame, setups.candidates, free)
              if np.isfinite(o.r_multiple)]
    dear_r = [o.r_multiple for o in label_all(context.frame, setups.candidates, dear)
              if np.isfinite(o.r_multiple)]
    assert np.mean(dear_r) < np.mean(free_r)


def test_mae_and_mfe_are_recorded(context):
    outcomes = [o for o in label_all(context.frame,
                                     detect_setups(context, RULES).candidates, RULES.setups)
                if o.outcome.is_resolved]
    assert outcomes
    assert all(o.mae_r >= 0 and o.mfe_r >= 0 for o in outcomes)


# ---------------------------------------------------------------- database

def test_the_store_round_trips_setups_and_outcomes(tmp_path, context):
    setups = detect_setups(context, RULES)
    outcomes = label_all(context.frame, setups.candidates, RULES.setups)

    with SetupStore(tmp_path / "smc.db") as store:
        run_id = store.start_run(symbol=context.symbol, timeframe=context.timeframe,
                                 rules_hash=RULES.rules_hash, bars=context.n_bars)
        stats = store.save_many(run_id, setups.candidates, outcomes, RULES.rules_hash)

        assert stats.inserted == len(setups.candidates)
        rows = store.query(symbol=context.symbol, resolved_only=False,
                           include_superseded=True)
        assert len(rows) == len(setups.candidates)
        assert "r_multiple" in rows.columns


def test_duplicate_inserts_are_skipped(tmp_path, context):
    setups = detect_setups(context, RULES)
    outcomes = label_all(context.frame, setups.candidates, RULES.setups)

    with SetupStore(tmp_path / "smc.db") as store:
        run_id = store.start_run(symbol=context.symbol, timeframe=context.timeframe,
                                 rules_hash=RULES.rules_hash, bars=context.n_bars)
        store.save_many(run_id, setups.candidates, outcomes, RULES.rules_hash)
        again = store.save_many(run_id, setups.candidates, outcomes, RULES.rules_hash)
        assert again.inserted == 0
        assert again.skipped == len(setups.candidates)


def test_the_as_of_filter_excludes_unresolved_trades(tmp_path, context):
    """The guard that makes historical probability honest."""
    setups = detect_setups(context, RULES)
    outcomes = label_all(context.frame, setups.candidates, RULES.setups)

    with SetupStore(tmp_path / "smc.db") as store:
        run_id = store.start_run(symbol=context.symbol, timeframe=context.timeframe,
                                 rules_hash=RULES.rules_hash, bars=context.n_bars)
        store.save_many(run_id, setups.candidates, outcomes, RULES.rules_hash)

        everything = store.query(symbol=context.symbol)
        if everything.empty:
            pytest.skip("no resolved setups in this fixture")

        cutoff = everything["resolved_at"].iloc[len(everything) // 2]
        earlier = store.query(symbol=context.symbol, resolved_before=cutoff)

        assert len(earlier) < len(everything)
        assert (earlier["resolved_at"] < cutoff).all()


def test_superseded_setups_are_excluded_by_default(tmp_path, context):
    setups = detect_setups(context, RULES)
    outcomes = label_all(context.frame, setups.candidates, RULES.setups)

    with SetupStore(tmp_path / "smc.db") as store:
        run_id = store.start_run(symbol=context.symbol, timeframe=context.timeframe,
                                 rules_hash=RULES.rules_hash, bars=context.n_bars)
        store.save_many(run_id, setups.candidates, outcomes, RULES.rules_hash)

        default = store.query(symbol=context.symbol, resolved_only=False)
        everything = store.query(symbol=context.symbol, resolved_only=False,
                                 include_superseded=True)
        assert len(default) <= len(everything)


def test_summary_reports_by_family(tmp_path, context):
    setups = detect_setups(context, RULES)
    outcomes = label_all(context.frame, setups.candidates, RULES.setups)

    with SetupStore(tmp_path / "smc.db") as store:
        run_id = store.start_run(symbol=context.symbol, timeframe=context.timeframe,
                                 rules_hash=RULES.rules_hash, bars=context.n_bars)
        store.save_many(run_id, setups.candidates, outcomes, RULES.rules_hash)
        summary = store.summary()
        if not summary.empty:
            assert {"family", "direction", "n"} <= set(summary.columns)
