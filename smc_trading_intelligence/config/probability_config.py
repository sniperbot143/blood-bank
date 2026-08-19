"""Probability estimation settings (docs/PROBABILITY_METHODOLOGY.md).

Separate from `smc_rules` on purpose: the SMC rules decide what a setup IS,
these decide what we are willing to CLAIM about it. Changing a prior should
never look like changing a detector.
"""

from __future__ import annotations

import hashlib
from enum import Enum

from pydantic import BaseModel, Field, model_validator


class Prior(str, Enum):
    JEFFREYS = "JEFFREYS"     # Beta(0.5, 0.5) -- the reference prior, default
    LAPLACE = "LAPLACE"       # Beta(1, 1)
    NONE = "NONE"             # raw frequency; honest only with a large n


class Reliability(str, Enum):
    VERY_LOW = "VERY_LOW"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    VERY_HIGH = "VERY_HIGH"

    @property
    def rank(self) -> int:
        return {"VERY_LOW": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "VERY_HIGH": 4}[self.value]

    def at_least(self, other: "Reliability") -> bool:
        return self.rank >= other.rank


class ProbabilityConfig(BaseModel):
    """How historical outcomes become a probability, and when they may not."""

    model_config = {"frozen": True}

    prior: Prior = Prior.JEFFREYS
    min_samples: int = Field(default=30, ge=1, le=100000)

    # Old data counts less. Half-life in days for the recency weight.
    recency_half_life_days: float = Field(default=365.0, gt=0.0, le=10000.0)
    recency_weighting: bool = True

    # Bootstrap the CI once there is enough data for it to mean anything.
    bootstrap_min_samples: int = Field(default=100, ge=10, le=100000)
    bootstrap_iterations: int = Field(default=1000, ge=100, le=100000)
    bootstrap_seed: int = 20240819
    # If the block bootstrap is this much wider than the analytic interval,
    # the analytic one is understating dependence and is discarded.
    bootstrap_width_ratio: float = Field(default=1.5, ge=1.0, le=10.0)

    # Reliability tiers (docs/PROBABILITY_METHODOLOGY.md §5).
    very_high_n: int = Field(default=500, ge=1)
    very_high_ci: float = Field(default=0.06, gt=0.0, le=1.0)
    high_n: int = Field(default=200, ge=1)
    high_ci: float = Field(default=0.10, gt=0.0, le=1.0)
    medium_n: int = Field(default=60, ge=1)
    medium_ci: float = Field(default=0.16, gt=0.0, le=1.0)
    low_n: int = Field(default=30, ge=1)
    recent_mass_months: int = Field(default=12, ge=1, le=600)
    recent_mass_required: float = Field(default=0.25, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _check(self) -> ProbabilityConfig:
        if not self.low_n <= self.medium_n <= self.high_n <= self.very_high_n:
            raise ValueError("reliability sample thresholds must be increasing")
        if not self.very_high_ci <= self.high_ci <= self.medium_ci:
            raise ValueError("reliability CI widths must be non-decreasing as tiers fall")
        return self

    @property
    def config_hash(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode("utf-8")).hexdigest()


DEFAULT_PROBABILITY_CONFIG = ProbabilityConfig()
