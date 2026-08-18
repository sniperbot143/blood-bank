"""Every SMC threshold lives here -- no magic numbers in detector code.

Rules are grouped per phase and validated by Pydantic. `SMCRules` is the
single object passed into detectors, and its `rules_hash` will be embedded in
every emitted signal so a result can always be tied back to the exact
parameters that produced it (docs/ARCHITECTURE.md section 6).
"""

from __future__ import annotations

import hashlib
from enum import Enum

from pydantic import BaseModel, Field, model_validator


class SwingMode(str, Enum):
    """How a swing point is identified.

    FRACTAL         fixed left/right window, strict on the left (default)
    FIXED_LOOKBACK  window extremum, ties allowed on both sides
    ATR_ADAPTIVE    window widens in high volatility, narrows in low
    """

    FRACTAL = "FRACTAL"
    FIXED_LOOKBACK = "FIXED_LOOKBACK"
    ATR_ADAPTIVE = "ATR_ADAPTIVE"


class SwingConfig(BaseModel):
    """Phase 2 -- swing detection (docs/SMC_DEFINITIONS.md section 1)."""

    model_config = {"frozen": True}

    mode: SwingMode = SwingMode.FRACTAL
    swing_left: int = Field(default=3, ge=1, le=50)
    swing_right: int = Field(default=3, ge=1, le=50)

    # A swing must stand at least this far from the previous opposite swing,
    # measured in ATR, or it is noise. ATR-scaled so the same number works on
    # EURUSDm and BTCUSDm.
    min_swing_atr: float = Field(default=0.5, ge=0.0, le=20.0)

    # ATR_ADAPTIVE only: window scale = clamp(ATR / rolling-median ATR).
    adaptive_reference_window: int = Field(default=200, ge=10, le=5000)
    adaptive_min_scale: float = Field(default=0.5, gt=0.0, le=1.0)
    adaptive_max_scale: float = Field(default=2.5, ge=1.0, le=10.0)

    # Flag swings whose confirmation window spans a data gap. Rejecting them
    # outright would discard every legitimate swing around a weekend close,
    # so the default records the fact and lets later phases decide.
    reject_across_gaps: bool = False

    @model_validator(mode="after")
    def _check_scales(self) -> SwingConfig:
        if self.adaptive_min_scale > self.adaptive_max_scale:
            raise ValueError("adaptive_min_scale must be <= adaptive_max_scale")
        return self

    @property
    def window(self) -> int:
        return self.swing_left + self.swing_right + 1


class SMCRules(BaseModel):
    """Top-level rule set. Later phases add their sections here."""

    model_config = {"frozen": True}

    atr_period: int = Field(default=14, ge=2, le=200)
    swing: SwingConfig = SwingConfig()

    @property
    def rules_hash(self) -> str:
        """Stable fingerprint of the whole rule set."""
        payload = self.model_dump_json()
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


DEFAULT_RULES = SMCRules()
