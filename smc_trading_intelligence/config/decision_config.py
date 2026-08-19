"""Confluence weights and decision thresholds (Phases 15-16).

Kept apart from `smc_rules` and `probability_config` because these are the
*policy* knobs -- how much evidence is enough to act -- rather than definitions
or estimators. The starting values come from docs/SMC_DEFINITIONS.md §30 and
docs/PROBABILITY_METHODOLOGY.md §9 and are explicitly un-optimised: Phase 19
tunes them on TRAIN/VALIDATION and reports out-of-sample.
"""

from __future__ import annotations

import hashlib

from pydantic import BaseModel, Field, model_validator


class ScoreWeights(BaseModel):
    """The 100 points of confluence. Each component scores continuously."""

    model_config = {"frozen": True}

    htf_bias: float = Field(default=15.0, ge=0.0, le=100.0)
    liquidity_sweep: float = Field(default=20.0, ge=0.0, le=100.0)
    mss: float = Field(default=20.0, ge=0.0, le=100.0)
    displacement: float = Field(default=10.0, ge=0.0, le=100.0)
    order_block: float = Field(default=10.0, ge=0.0, le=100.0)
    fvg: float = Field(default=10.0, ge=0.0, le=100.0)
    premium_discount: float = Field(default=5.0, ge=0.0, le=100.0)
    session: float = Field(default=5.0, ge=0.0, le=100.0)
    risk_reward: float = Field(default=5.0, ge=0.0, le=100.0)

    # Component shaping
    sweep_full_magnitude_atr: float = Field(default=0.5, gt=0.0, le=10.0)
    ob_max_age_bars: int = Field(default=50, ge=1, le=5000)
    fvg_max_age_bars: int = Field(default=50, ge=1, le=5000)
    rr_full: float = Field(default=3.0, gt=0.0, le=50.0)
    preferred_sessions: list[str] = Field(default_factory=lambda: ["LONDON", "NY_AM"])

    @property
    def total(self) -> float:
        return (self.htf_bias + self.liquidity_sweep + self.mss + self.displacement
                + self.order_block + self.fvg + self.premium_discount + self.session
                + self.risk_reward)

    @model_validator(mode="after")
    def _check_total(self) -> ScoreWeights:
        if abs(self.total - 100.0) > 1e-9:
            raise ValueError(f"confluence weights must sum to 100, got {self.total}")
        return self


class DecisionConfig(BaseModel):
    """When evidence becomes an instruction. Starting values, not final ones."""

    model_config = {"frozen": True}

    weights: ScoreWeights = ScoreWeights()

    # Tiers
    strong_probability: float = Field(default=0.72, ge=0.0, le=1.0)
    strong_score: float = Field(default=85.0, ge=0.0, le=100.0)
    strong_rr: float = Field(default=2.0, ge=0.0, le=100.0)

    normal_probability: float = Field(default=0.63, ge=0.0, le=1.0)
    normal_score: float = Field(default=70.0, ge=0.0, le=100.0)
    normal_rr: float = Field(default=1.5, ge=0.0, le=100.0)

    weak_probability: float = Field(default=0.55, ge=0.0, le=1.0)
    weak_score: float = Field(default=55.0, ge=0.0, le=100.0)
    weak_rr: float = Field(default=1.5, ge=0.0, le=100.0)

    # Hard vetoes
    min_rr: float = Field(default=1.5, ge=0.0, le=100.0)
    require_positive_expectancy: bool = True
    htf_veto: bool = True
    max_stop_atr: float = Field(default=3.0, gt=0.0, le=100.0)
    min_reliability_for_trade: str = "MEDIUM"
    min_reliability_for_strong: str = "HIGH"

    @model_validator(mode="after")
    def _check_tiers(self) -> DecisionConfig:
        if not (self.weak_probability <= self.normal_probability <= self.strong_probability):
            raise ValueError("tier probabilities must increase weak -> normal -> strong")
        if not (self.weak_score <= self.normal_score <= self.strong_score):
            raise ValueError("tier scores must increase weak -> normal -> strong")
        return self

    @property
    def config_hash(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode("utf-8")).hexdigest()


DEFAULT_DECISION_CONFIG = DecisionConfig()
