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


class StructureConfig(BaseModel):
    """Phase 3 -- market structure (docs/SMC_DEFINITIONS.md section 2)."""

    model_config = {"frozen": True}

    # Two highs within this many ATR of each other are "equal", not HH/LH.
    equal_tolerance_atr: float = Field(default=0.05, ge=0.0, le=5.0)

    # A dealing range narrower than this is a range, whatever the labels say.
    range_atr_mult: float = Field(default=2.0, ge=0.0, le=50.0)

    # Internal structure: the same algorithm on a finer swing setting.
    track_internal: bool = True
    internal_left: int = Field(default=1, ge=1, le=50)
    internal_right: int = Field(default=1, ge=1, le=50)
    internal_min_swing_atr: float = Field(default=0.0, ge=0.0, le=20.0)


class BOSMode(str, Enum):
    """What counts as "through" a level."""

    CLOSE_ONLY = "CLOSE_ONLY"                          # default: a close beyond it
    WICK_OR_CLOSE = "WICK_OR_CLOSE"                    # a touch is enough
    DISPLACEMENT_CONFIRMATION = "DISPLACEMENT_CONFIRMATION"  # close + displacement


class DisplacementConfig(BaseModel):
    """Phase 4 (v1) -- displacement (docs/SMC_DEFINITIONS.md section 6).

    Phase 7 completes this: the imbalance component needs FVGs (Phase 8) and
    multi-bar runs arrive with it. Weights are config, not code, so switching
    the fourth component on is a visible, hash-changing decision rather than a
    silent shift in what "STRONG" means.
    """

    model_config = {"frozen": True}

    body_weight: float = Field(default=0.50, ge=0.0, le=1.0)
    range_weight: float = Field(default=0.25, ge=0.0, le=1.0)
    close_weight: float = Field(default=0.25, ge=0.0, le=1.0)
    imbalance_weight: float = Field(default=0.0, ge=0.0, le=1.0)  # Phase 8 turns this on

    # Value of each raw measure at which its component scores a full 1.0.
    body_atr_full: float = Field(default=1.0, gt=0.0, le=20.0)
    range_atr_full: float = Field(default=1.5, gt=0.0, le=20.0)
    close_location_min: float = Field(default=0.70, ge=0.0, lt=1.0)

    weak_threshold: float = Field(default=0.35, ge=0.0, le=1.0)
    moderate_threshold: float = Field(default=0.55, ge=0.0, le=1.0)
    strong_threshold: float = Field(default=0.75, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _check(self) -> DisplacementConfig:
        total = self.body_weight + self.range_weight + self.close_weight + self.imbalance_weight
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"displacement weights must sum to 1.0, got {total}")
        if not self.weak_threshold <= self.moderate_threshold <= self.strong_threshold:
            raise ValueError("displacement thresholds must be weak <= moderate <= strong")
        return self


class BreakConfig(BaseModel):
    """Phase 4 -- BOS / CHOCH / MSS (docs/SMC_DEFINITIONS.md sections 3-5)."""

    model_config = {"frozen": True}

    bos_mode: BOSMode = BOSMode.CLOSE_ONLY

    # A bar that opens after missing data is not evidence of a break.
    reject_on_gap_bar: bool = True

    # MSS = CHOCH + displacement. The CHOCH stays pending this many bars for a
    # displaced close through the same level before it expires.
    mss_min_displacement: float = Field(default=0.55, ge=0.0, le=1.0)
    mss_confirm_window: int = Field(default=10, ge=0, le=500)
    mss_min_legs: int = Field(default=2, ge=0, le=20)

    # Requires liquidity (Phase 6). Off until then rather than silently ignored.
    mss_require_swept_origin: bool = False


class SMCRules(BaseModel):
    """Top-level rule set. Later phases add their sections here."""

    model_config = {"frozen": True}

    atr_period: int = Field(default=14, ge=2, le=200)
    swing: SwingConfig = SwingConfig()
    structure: StructureConfig = StructureConfig()
    displacement: DisplacementConfig = DisplacementConfig()
    breaks: BreakConfig = BreakConfig()

    def internal_swing_config(self) -> SwingConfig:
        """The finer swing setting used for internal structure."""
        return self.swing.model_copy(
            update={
                "swing_left": self.structure.internal_left,
                "swing_right": self.structure.internal_right,
                "min_swing_atr": self.structure.internal_min_swing_atr,
            }
        )

    @property
    def rules_hash(self) -> str:
        """Stable fingerprint of the whole rule set."""
        payload = self.model_dump_json()
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


DEFAULT_RULES = SMCRules()
