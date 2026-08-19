"""Paper trading, the live-trading gates, and the optional Claude layer."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from backtesting.labeling import label_all
from config.decision_config import DecisionConfig
from config.probability_config import ProbabilityConfig
from config.smc_rules import SMCRules, SetupConfig, StructureConfig, SwingConfig
from database.models import SetupStore
from execution.broker import CloseReason, OrderState, PaperBroker
from execution.live import CONFIRMATION_PHRASE, LiveTradingDisabled, is_enabled, preflight
from features.context import MarketContext
from optional_ai.claude import local_narration, narrate
from probability.probability import estimate_from_rows
from signals.decision_engine import decide
from signals.setups import detect_setups
from tests.conftest import make_market_frame

RULES = SMCRules(
    swing=SwingConfig(swing_left=2, swing_right=2, min_swing_atr=0.5),
    structure=StructureConfig(track_internal=False),
    setups=SetupConfig(max_hold_bars=40),
)


@pytest.fixture(scope="module")
def signal_fixture():
    frame = make_market_frame(900, 11)
    context = MarketContext.build(frame, RULES)
    setups = detect_setups(context, RULES)
    candidate = setups.tradeable()[0]

    rows = pd.DataFrame({
        "outcome": ["TP1_FIRST"] * 400 + ["SL_FIRST"] * 200,
        "r_multiple": [2.0] * 400 + [-1.0] * 200,
        "mae_r": [0.3] * 600, "mfe_r": [1.5] * 600, "bars_to_result": [10] * 600,
        "resolved_at": pd.date_range("2023-01-01", periods=600, freq="D", tz="UTC"),
    })
    estimate = estimate_from_rows(rows, "tp1", rows["outcome"] == "TP1_FIRST",
                                  tier="T2", config=ProbabilityConfig(min_samples=10))
    lenient = DecisionConfig(strong_score=5.0, normal_score=1.0, weak_score=0.0,
                             strong_rr=1.0, normal_rr=1.0, weak_rr=1.0, min_rr=1.0)
    signal = decide(candidate, estimate, config=lenient)
    return frame, signal


# ------------------------------------------------------------ paper broker

def test_a_limit_order_waits_to_be_filled(signal_fixture):
    frame, signal = signal_fixture
    broker = PaperBroker()
    position = broker.place(signal, volume=1.0, expires_in_bars=20)

    assert position.state is OrderState.PENDING
    assert broker.open_positions() == []
    assert broker.pending_orders() == [position]


def test_an_unfilled_order_expires(signal_fixture):
    frame, signal = signal_fixture
    broker = PaperBroker()
    position = broker.place(signal, expires_in_bars=1)
    start = signal.candidate.signal_index

    # A bar far from the entry, past the expiry window.
    far = frame.iloc[start + 1].copy()
    far["high"] = position.entry + 1000
    far["low"] = position.entry + 900
    broker.on_bar(far, start + 5)

    assert position.state is OrderState.CANCELLED
    assert position.close_reason is CloseReason.EXPIRED


def test_a_touched_limit_fills_then_a_target_closes_it(signal_fixture):
    frame, signal = signal_fixture
    broker = PaperBroker()
    position = broker.place(signal, volume=1.0, expires_in_bars=50)
    bullish = position.bullish

    fill_bar = frame.iloc[0].copy()
    fill_bar["high"] = position.entry + 0.1
    fill_bar["low"] = position.entry - 0.1
    broker.on_bar(fill_bar, 0)
    assert position.state is OrderState.OPEN

    target_bar = frame.iloc[1].copy()
    if bullish:
        target_bar["high"] = position.take_profit + 0.5
        target_bar["low"] = position.entry
    else:
        target_bar["low"] = position.take_profit - 0.5
        target_bar["high"] = position.entry
    broker.on_bar(target_bar, 1)

    assert position.state is OrderState.CLOSED
    assert position.close_reason is CloseReason.TAKE_PROFIT
    assert position.r_multiple > 0
    assert broker.balance > broker.starting_balance


def test_a_stop_closes_at_minus_one_r(signal_fixture):
    frame, signal = signal_fixture
    broker = PaperBroker()
    position = broker.place(signal, volume=1.0, expires_in_bars=50)

    fill_bar = frame.iloc[0].copy()
    fill_bar["high"] = position.entry + 0.1
    fill_bar["low"] = position.entry - 0.1
    broker.on_bar(fill_bar, 0)

    stop_bar = frame.iloc[1].copy()
    if position.bullish:
        stop_bar["low"] = position.stop_loss - 0.5
        stop_bar["high"] = position.entry
    else:
        stop_bar["high"] = position.stop_loss + 0.5
        stop_bar["low"] = position.entry
    broker.on_bar(stop_bar, 1)

    assert position.close_reason is CloseReason.STOP_LOSS
    assert position.r_multiple == pytest.approx(-1.0, abs=0.05)


def test_an_ambiguous_bar_is_recorded_as_a_stop(signal_fixture):
    """Same rule as the labeller: unknown intrabar order means assume the worst."""
    frame, signal = signal_fixture
    broker = PaperBroker()
    position = broker.place(signal, volume=1.0, expires_in_bars=50)

    fill_bar = frame.iloc[0].copy()
    fill_bar["high"] = position.entry + 0.1
    fill_bar["low"] = position.entry - 0.1
    broker.on_bar(fill_bar, 0)

    both = frame.iloc[1].copy()
    both["high"] = max(position.take_profit, position.stop_loss) + 1
    both["low"] = min(position.take_profit, position.stop_loss) - 1
    broker.on_bar(both, 1)

    assert position.close_reason is CloseReason.STOP_LOSS
    assert "ambiguous" in position.comment


def test_every_event_is_journalled(signal_fixture, tmp_path):
    frame, signal = signal_fixture
    journal = tmp_path / "paper.jsonl"
    broker = PaperBroker(journal_path=journal)
    position = broker.place(signal, volume=1.0)

    fill_bar = frame.iloc[0].copy()
    fill_bar["high"] = position.entry + 0.1
    fill_bar["low"] = position.entry - 0.1
    broker.on_bar(fill_bar, 0)

    lines = [json.loads(line) for line in journal.read_text().splitlines()]
    assert [line["event"] for line in lines] == ["PLACED", "FILLED"]
    assert lines[0]["decision"] == signal.decision.value
    assert lines[0]["probability"] is not None


def test_the_paper_broker_reports_its_state(signal_fixture):
    frame, signal = signal_fixture
    broker = PaperBroker()
    broker.place(signal)
    assert "balance" in broker.summary()
    assert len(broker.to_frame()) == 1


# ------------------------------------------------------------- live gates

def test_live_trading_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ENABLE_LIVE", raising=False)
    assert not is_enabled()


def test_constructing_a_live_broker_without_the_flag_raises(monkeypatch):
    monkeypatch.delenv("ENABLE_LIVE", raising=False)
    from execution.live import LiveBroker

    with pytest.raises(LiveTradingDisabled, match="disabled"):
        LiveBroker(symbol="XAUUSDm", confirm=CONFIRMATION_PHRASE)


def test_the_flag_alone_is_not_enough(monkeypatch):
    monkeypatch.setenv("ENABLE_LIVE", "true")
    from execution.live import LiveBroker

    with pytest.raises(LiveTradingDisabled, match="confirm="):
        LiveBroker(symbol="XAUUSDm")          # no confirmation phrase


def test_a_wrong_confirmation_phrase_is_not_accepted(monkeypatch):
    monkeypatch.setenv("ENABLE_LIVE", "true")
    from execution.live import LiveBroker

    with pytest.raises(LiveTradingDisabled):
        LiveBroker(symbol="XAUUSDm", confirm="yes please")


def test_preflight_reports_what_it_cannot_check(monkeypatch):
    monkeypatch.delenv("ENABLE_LIVE", raising=False)
    report = preflight()

    assert not report.passed
    assert "ENABLE_LIVE is set" in report.checks
    assert report.manual                      # the human-only preconditions
    assert "walk-forward" in report.summary()


def test_the_analysis_pipeline_does_not_import_live():
    """Deleting execution/ must not break analysis."""
    import inspect

    import features.context as context_module
    import signals.decision_engine as decision_module

    for module in (context_module, decision_module):
        source = inspect.getsource(module)
        assert "execution.live" not in source
        assert "LiveBroker" not in source


# --------------------------------------------------------- optional Claude

def test_narration_works_with_no_api_key(monkeypatch, signal_fixture):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ENABLE_CLAUDE", raising=False)
    frame, signal = signal_fixture

    result = narrate(signal)
    assert result.source == "local"
    assert "MARKET NARRATIVE" in result.text
    assert "REASON FOR THE DECISION" in result.text


def test_the_local_narration_quotes_the_engines_own_numbers(signal_fixture):
    frame, signal = signal_fixture
    text = local_narration(signal)

    assert str(signal.score.rounded) in text
    assert f"{signal.probability.sample_size:,}" in text
    assert signal.decision.value in text


def test_claude_stays_off_unless_both_switches_are_on(monkeypatch):
    from optional_ai.claude import is_enabled as claude_enabled

    monkeypatch.setenv("ENABLE_CLAUDE", "true")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert not claude_enabled()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("ENABLE_CLAUDE", "false")
    assert not claude_enabled()

    monkeypatch.setenv("ENABLE_CLAUDE", "true")
    assert claude_enabled()


def test_a_broken_api_falls_back_instead_of_failing(monkeypatch, signal_fixture):
    frame, signal = signal_fixture
    monkeypatch.setenv("ENABLE_CLAUDE", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-invalid")

    result = narrate(signal)          # no network here: must degrade, not raise
    assert result.source == "local"
    assert result.error
    assert "MARKET NARRATIVE" in result.text
