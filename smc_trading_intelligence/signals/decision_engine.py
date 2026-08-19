"""The decision engine: evidence in, an instruction out -- or NO_TRADE.

Order of operations (docs/PROBABILITY_METHODOLOGY.md §9):

    1. hard vetoes   any one of them ends it, with a reason code
    2. tiering       probability AND score AND R:R AND reliability, together

`NO_TRADE` is a valid, frequent, correct output. The engine is decisive when
the evidence is there and silent when it is not; a system that always has an
opinion is a system whose opinion is worthless.

Every decision carries `reason_codes`, so any signal can be audited without
re-running the engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import pandas as pd

from config.decision_config import DEFAULT_DECISION_CONFIG, DecisionConfig
from config.probability_config import Reliability
from probability.probability import ProbabilityEstimate
from signals.confluence import SetupScore, score_setup
from signals.setups import SetupCandidate


class Decision(str, Enum):
    STRONG_BUY = "STRONG_BUY"
    BUY = "BUY"
    WEAK_BUY = "WEAK_BUY"
    NO_TRADE = "NO_TRADE"
    WEAK_SELL = "WEAK_SELL"
    SELL = "SELL"
    STRONG_SELL = "STRONG_SELL"

    @property
    def is_trade(self) -> bool:
        return self is not Decision.NO_TRADE

    @property
    def is_strong(self) -> bool:
        return self in (Decision.STRONG_BUY, Decision.STRONG_SELL)


_TIER_NAMES = {
    ("STRONG", True): Decision.STRONG_BUY, ("STRONG", False): Decision.STRONG_SELL,
    ("NORMAL", True): Decision.BUY, ("NORMAL", False): Decision.SELL,
    ("WEAK", True): Decision.WEAK_BUY, ("WEAK", False): Decision.WEAK_SELL,
}


@dataclass
class Signal:
    """The final output object -- the JSON contract from SMC_DEFINITIONS §35."""

    decision: Decision
    candidate: SetupCandidate
    score: SetupScore
    probability: ProbabilityEstimate
    reason_codes: list[str] = field(default_factory=list)
    expectancy: float = float("nan")
    rules_hash: str = ""
    decision_config_hash: str = ""

    @property
    def symbol(self) -> str:
        return self.candidate.symbol

    @property
    def timestamp(self) -> pd.Timestamp:
        return self.candidate.signal_time

    def as_dict(self) -> dict:
        levels = self.candidate.levels
        values = self.candidate.features.values
        payload = {
            "decision": self.decision.value,
            "probability": (round(float(self.probability.probability), 4)
                            if np.isfinite(self.probability.probability) else None),
            "probability_source": self.probability.source,
            "probability_reliability": self.probability.reliability.value,
            "sample_size": self.probability.sample_size,
            "effective_sample_size": round(self.probability.effective_sample_size, 1),
            "confidence_interval_95": [round(x, 4) for x in
                                       self.probability.confidence_interval_95],
            "similarity_tier": self.probability.similarity_tier,
            "setup_score": self.score.rounded,
            "symbol": self.candidate.symbol,
            "timeframe": self.candidate.timeframe,
            "timestamp": self.candidate.signal_time.isoformat(),
            "direction": self.candidate.direction,
            "setup_type": self.candidate.setup_type,
            "entry": levels.entry,
            "stop_loss": levels.stop_loss,
            "take_profit_1": levels.take_profit_1,
            "take_profit_2": levels.take_profit_2,
            "take_profit_3": levels.take_profit_3,
            "risk_reward": round(float(levels.rr1), 2) if np.isfinite(levels.rr1) else None,
            "expectancy_r": round(float(self.expectancy), 3) if np.isfinite(self.expectancy) else None,
            "htf_bias": {k: v for k, v in values.items() if k.startswith("htf_") and
                         k.endswith("_bias")},
            "liquidity_event": values.get("liquidity_event"),
            "structure_event": values.get("structure_event"),
            "market_regime": values.get("regime_key"),
            "session": values.get("session"),
            "pd_zone": values.get("pd_zone"),
            "invalidation": _invalidation(self.candidate),
            "reason_codes": self.reason_codes,
            "rules_hash": self.rules_hash,
            "decision_config_hash": self.decision_config_hash,
        }
        return payload

    def describe(self) -> str:
        levels = self.candidate.levels
        probability = self.probability
        lines = [
            "=" * 46,
            f"{self.candidate.symbol} — {self.candidate.timeframe}   "
            f"{self.candidate.signal_time:%Y-%m-%d %H:%M} UTC",
            "",
            f"DECISION        : {self.decision.value}",
            f"PROBABILITY     : "
            + (f"{probability.probability:.1%}" if np.isfinite(probability.probability) else "n/a"),
            f"RELIABILITY     : {probability.reliability.value}",
            f"SAMPLE          : {probability.sample_size:,} "
            f"(effective {probability.effective_sample_size:.0f}, tier {probability.similarity_tier})",
            f"95% CI          : [{probability.confidence_interval_95[0]:.3f}, "
            f"{probability.confidence_interval_95[1]:.3f}] ({probability.ci_method})",
            f"SETUP SCORE     : {self.score.rounded} / 100",
            f"SETUP TYPE      : {self.candidate.setup_type}",
        ]
        if self.decision.is_trade:
            lines += [
                f"ENTRY           : {levels.entry:.5f}",
                f"STOP LOSS       : {levels.stop_loss:.5f}",
                f"TP1 / TP2       : {levels.take_profit_1:.5f} / {levels.take_profit_2:.5f}",
                f"R:R             : 1:{levels.rr1:.2f}",
                f"INVALIDATION    : {_invalidation(self.candidate)}",
            ]
        lines += [f"REASONS         : {', '.join(self.reason_codes) or '-'}", "=" * 46]
        return "\n".join(lines)


def _invalidation(candidate: SetupCandidate) -> str:
    side = "above" if candidate.bullish else "below"
    return (f"{candidate.timeframe} close {'below' if candidate.bullish else 'above'} "
            f"{candidate.levels.stop_loss:.5f}")


def decide(
    candidate: SetupCandidate,
    probability: ProbabilityEstimate,
    *,
    score: SetupScore | None = None,
    config: DecisionConfig = DEFAULT_DECISION_CONFIG,
    rules_hash: str = "",
) -> Signal:
    """Combine confluence, probability, R:R and reliability into one call."""
    score = score if score is not None else score_setup(candidate, config)
    reasons: list[str] = []
    levels = candidate.levels
    p = probability.probability
    rr = levels.rr1

    expectancy = float("nan")
    if np.isfinite(p) and np.isfinite(rr):
        expectancy = p * rr - (1.0 - p)

    def veto(code: str) -> Signal:
        reasons.append(code)
        return Signal(decision=Decision.NO_TRADE, candidate=candidate, score=score,
                      probability=probability, reason_codes=reasons, expectancy=expectancy,
                      rules_hash=rules_hash, decision_config_hash=config.config_hash)

    # -- hard vetoes -------------------------------------------------------
    if not levels.is_valid:
        return veto(f"INVALID_GEOMETRY_{levels.invalid_reason}")
    if not np.isfinite(p):
        return veto("INSUFFICIENT_SAMPLE")

    minimum = Reliability(config.min_reliability_for_trade)
    if not probability.reliability.at_least(minimum):
        return veto(f"LOW_RELIABILITY_{probability.reliability.value}")
    if not np.isfinite(rr) or rr < config.min_rr:
        return veto("RR_BELOW_MINIMUM")
    if config.require_positive_expectancy and expectancy <= 0:
        return veto("NEGATIVE_EXPECTANCY")
    if np.isfinite(levels.risk_atr) and levels.risk_atr > config.max_stop_atr:
        return veto("STOP_TOO_WIDE")
    if config.htf_veto and candidate.features.values.get("htf_bias_agreement") == "OPPOSED":
        return veto("HTF_CONFLICT")

    # -- tiering -----------------------------------------------------------
    bullish = candidate.bullish
    strong_reliability = Reliability(config.min_reliability_for_strong)

    if (p >= config.strong_probability and score.total >= config.strong_score
            and rr >= config.strong_rr and probability.reliability.at_least(strong_reliability)):
        tier = "STRONG"
    elif (p >= config.normal_probability and score.total >= config.normal_score
          and rr >= config.normal_rr):
        tier = "NORMAL"
    elif (p >= config.weak_probability and score.total >= config.weak_score
          and rr >= config.weak_rr):
        tier = "WEAK"
    else:
        reasons.append("BELOW_THRESHOLDS")
        reasons.append(f"p={p:.3f} score={score.rounded} rr={rr:.2f}")
        return Signal(decision=Decision.NO_TRADE, candidate=candidate, score=score,
                      probability=probability, reason_codes=reasons, expectancy=expectancy,
                      rules_hash=rules_hash, decision_config_hash=config.config_hash)

    reasons.append(f"TIER_{tier}")
    reasons.append(f"p={p:.3f}")
    reasons.append(f"score={score.rounded}")
    reasons.append(f"rr={rr:.2f}")
    reasons.append(f"reliability={probability.reliability.value}")
    reasons.append(f"tier={probability.similarity_tier}")

    return Signal(decision=_TIER_NAMES[(tier, bullish)], candidate=candidate, score=score,
                  probability=probability, reason_codes=reasons, expectancy=expectancy,
                  rules_hash=rules_hash, decision_config_hash=config.config_hash)
