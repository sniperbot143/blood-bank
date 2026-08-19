"""Every SMC threshold lives here -- no magic numbers in detector code.

Rules are grouped per phase and validated by Pydantic. `SMCRules` is the
single object passed into detectors, and its `rules_hash` will be embedded in
every emitted signal so a result can always be tied back to the exact
parameters that produced it (docs/ARCHITECTURE.md section 6).
"""

from __future__ import annotations

import hashlib
from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator


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

    # Phase 8 switched the imbalance component on; these are the §6 weights.
    # The change is visible in `rules_hash`, which is the whole point of
    # keeping them in config rather than in code.
    body_weight: float = Field(default=0.40, ge=0.0, le=1.0)
    range_weight: float = Field(default=0.20, ge=0.0, le=1.0)
    close_weight: float = Field(default=0.20, ge=0.0, le=1.0)
    imbalance_weight: float = Field(default=0.20, ge=0.0, le=1.0)

    # Value of each raw measure at which its component scores a full 1.0.
    body_atr_full: float = Field(default=1.0, gt=0.0, le=20.0)
    range_atr_full: float = Field(default=1.5, gt=0.0, le=20.0)
    close_location_min: float = Field(default=0.70, ge=0.0, lt=1.0)

    # Phase 7: a displacement leg may span several consecutive same-direction
    # bars. The run ending at bar i is scored as one synthetic bar.
    max_run_bars: int = Field(default=3, ge=1, le=20)
    require_same_direction_run: bool = True

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


class SessionWindow(BaseModel):
    """One trading session, defined in a named timezone so DST is handled.

    `start`/`end` are local "HH:MM" strings. A window whose end is at or before
    its start wraps past midnight.
    """

    model_config = {"frozen": True}

    name: str
    tz: str = "UTC"
    start: str = "00:00"
    end: str = "07:00"
    enabled: bool = True

    @field_validator("start", "end")
    @classmethod
    def _validate_time(cls, v: str) -> str:
        try:
            hour, minute = (int(part) for part in v.split(":"))
        except ValueError as exc:
            raise ValueError(f"time must be 'HH:MM', got {v!r}") from exc
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(f"time out of range: {v!r}")
        return f"{hour:02d}:{minute:02d}"

    @property
    def start_minutes(self) -> int:
        hour, minute = (int(p) for p in self.start.split(":"))
        return hour * 60 + minute

    @property
    def end_minutes(self) -> int:
        hour, minute = (int(p) for p in self.end.split(":"))
        return hour * 60 + minute

    @property
    def wraps_midnight(self) -> bool:
        return self.end_minutes <= self.start_minutes


DEFAULT_SESSIONS: list[SessionWindow] = [
    SessionWindow(name="ASIAN", tz="UTC", start="00:00", end="07:00"),
    SessionWindow(name="LONDON", tz="UTC", start="07:00", end="12:00"),
    SessionWindow(name="NY_AM", tz="UTC", start="12:00", end="15:00"),
    SessionWindow(name="NY_PM", tz="UTC", start="15:00", end="20:00"),
]


class SessionConfig(BaseModel):
    """Phase 5 -- session windows (docs/SMC_DEFINITIONS.md section 13)."""

    model_config = {"frozen": True}

    windows: list[SessionWindow] = Field(default_factory=lambda: list(DEFAULT_SESSIONS))

    @property
    def active(self) -> list[SessionWindow]:
        return [w for w in self.windows if w.enabled]


class LiquidityConfig(BaseModel):
    """Phase 5 -- liquidity pools (docs/SMC_DEFINITIONS.md section 7).

    Every tolerance is ATR- or time-based, never a fixed pip count: the same
    numbers have to behave on EURUSDm (0.0001 tick) and BTCUSDm (1.0 tick).
    """

    model_config = {"frozen": True}

    track_swing_pools: bool = True
    track_equal_levels: bool = True
    track_daily: bool = True
    track_weekly: bool = True
    track_sessions: bool = True

    # Equal highs/lows
    equal_tolerance_atr: float = Field(default=0.10, ge=0.0, le=5.0)
    equal_max_gap_bars: int = Field(default=50, ge=1, le=5000)
    equal_min_members: int = Field(default=2, ge=2, le=10)

    # Lifecycle
    touch_tolerance_atr: float = Field(default=0.0, ge=0.0, le=1.0)
    sweep_min_penetration_atr: float = Field(default=0.02, ge=0.0, le=5.0)

    # Calendar levels. Broker days rarely start at 00:00 UTC.
    day_start_hour: int = Field(default=0, ge=0, le=23)
    week_start_weekday: int = Field(default=0, ge=0, le=6)   # 0 = Monday

    # Strength model (docs/PHASE_5_PLAN.md §4). Bases by pool type, plus
    # increments for each retest and each extra equal-level member.
    strength_swing: float = Field(default=1.0, ge=0.0, le=10.0)
    strength_equal: float = Field(default=2.0, ge=0.0, le=10.0)
    strength_daily: float = Field(default=2.0, ge=0.0, le=10.0)
    strength_weekly: float = Field(default=3.0, ge=0.0, le=10.0)
    strength_session: float = Field(default=1.5, ge=0.0, le=10.0)
    strength_per_touch: float = Field(default=0.25, ge=0.0, le=5.0)
    strength_max_touches: int = Field(default=4, ge=0, le=50)
    strength_per_extra_member: float = Field(default=0.50, ge=0.0, le=5.0)


class FVGConfig(BaseModel):
    """Phase 8 -- fair value gaps (docs/SMC_DEFINITIONS.md sections 9-10)."""

    model_config = {"frozen": True}

    min_size_atr: float = Field(default=0.10, ge=0.0, le=10.0)
    require_displacement: bool = True
    min_displacement: float = Field(default=0.35, ge=0.0, le=1.0)

    # Fill fraction at which a gap counts as mitigated. 0.5 = consequent
    # encroachment, the level most SMC traders actually defend.
    mitigated_fill: float = Field(default=0.50, gt=0.0, le=1.0)

    # IFVG: a gap fully invalidated by a close beyond it, then reclaimed.
    ifvg_reclaim_bars: int = Field(default=10, ge=1, le=200)


class OBZone(str, Enum):
    FULL_RANGE = "FULL_RANGE"       # high to low of the origin bar (default)
    BODY = "BODY"                   # open to close
    WICK_TO_BODY = "WICK_TO_BODY"   # the wick edge to the body edge


class OrderBlockConfig(BaseModel):
    """Phase 9 -- order blocks (docs/SMC_DEFINITIONS.md section 11)."""

    model_config = {"frozen": True}

    zone: OBZone = OBZone.FULL_RANGE
    max_lookback: int = Field(default=5, ge=1, le=50)
    min_size_atr: float = Field(default=0.20, ge=0.0, le=10.0)
    min_displacement: float = Field(default=0.35, ge=0.0, le=1.0)

    # Fraction of the zone price must trade into to count as mitigated.
    mitigation_fill: float = Field(default=0.50, gt=0.0, le=1.0)

    # A failed OB becomes a breaker only once price returns to it from the
    # other side; this is how long that retest may take.
    breaker_retest_bars: int = Field(default=50, ge=1, le=1000)
    track_breakers: bool = True


class RangeConfig(BaseModel):
    """Phase 10 -- dealing range and premium/discount (sections 12, 22)."""

    model_config = {"frozen": True}

    min_range_atr: float = Field(default=2.0, ge=0.0, le=100.0)
    equilibrium_band: float = Field(default=0.03, ge=0.0, le=0.5)
    ote_low: float = Field(default=0.62, ge=0.0, le=1.0)
    ote_high: float = Field(default=0.79, ge=0.0, le=1.0)
    report_ote: bool = True

    @model_validator(mode="after")
    def _check(self) -> RangeConfig:
        if self.ote_low > self.ote_high:
            raise ValueError("ote_low must be <= ote_high")
        return self


class RegimeConfig(BaseModel):
    """Phase 11 -- market regime (docs/SMC_DEFINITIONS.md section 14)."""

    model_config = {"frozen": True}

    lookback: int = Field(default=500, ge=20, le=20000)
    low_vol_percentile: float = Field(default=0.33, ge=0.0, le=1.0)
    high_vol_percentile: float = Field(default=0.66, ge=0.0, le=1.0)
    adx_period: int = Field(default=14, ge=2, le=200)
    range_adx: float = Field(default=20.0, ge=0.0, le=100.0)
    trend_adx: float = Field(default=30.0, ge=0.0, le=100.0)

    @model_validator(mode="after")
    def _check(self) -> RegimeConfig:
        if self.low_vol_percentile > self.high_vol_percentile:
            raise ValueError("low_vol_percentile must be <= high_vol_percentile")
        if self.range_adx > self.trend_adx:
            raise ValueError("range_adx must be <= trend_adx")
        return self


class MTFConfig(BaseModel):
    """Phase 11 -- multi-timeframe engine (docs/SMC_DEFINITIONS.md section 23)."""

    model_config = {"frozen": True}

    higher_timeframes: list[str] = Field(default_factory=lambda: ["M15", "H1"])
    htf_veto: bool = True        # a directly opposing HTF bias blocks a setup


class SetupConfig(BaseModel):
    """Phase 13 -- setup construction, entries, stops and targets."""

    model_config = {"frozen": True}

    # How recently the ingredients must have happened, in bars.
    sweep_lookback: int = Field(default=20, ge=1, le=500)
    break_lookback: int = Field(default=20, ge=1, le=500)
    poi_max_distance_atr: float = Field(default=2.0, gt=0.0, le=50.0)

    # Entry
    entry_at_poi_mid: bool = True          # else the far edge of the zone
    entry_valid_bars: int = Field(default=12, ge=1, le=500)

    # Stop loss
    stop_buffer_atr: float = Field(default=0.25, ge=0.0, le=10.0)
    min_stop_atr: float = Field(default=0.20, gt=0.0, le=50.0)
    max_stop_atr: float = Field(default=3.0, gt=0.0, le=100.0)

    # Targets
    tp1_rr: float = Field(default=1.5, gt=0.0, le=100.0)
    tp2_rr: float = Field(default=3.0, gt=0.0, le=100.0)
    tp3_rr: float = Field(default=5.0, gt=0.0, le=100.0)
    prefer_liquidity_targets: bool = True
    min_rr: float = Field(default=1.5, gt=0.0, le=100.0)

    # Outcome labelling
    max_hold_bars: int = Field(default=96, ge=1, le=10000)

    # Costs, applied at labelling time so a gross-only edge cannot survive.
    spread_cost_atr: float = Field(default=0.02, ge=0.0, le=5.0)
    slippage_atr: float = Field(default=0.01, ge=0.0, le=5.0)

    # One open setup per symbol/direction; later candidates are superseded.
    deoverlap: bool = True


class SweepConfig(BaseModel):
    """Phase 6 -- liquidity sweeps (docs/SMC_DEFINITIONS.md section 8).

    A sweep is: liquidity exists + price trades through it + price rejects.
    All three are required; two of them is just a level being broken.
    """

    model_config = {"frozen": True}

    min_penetration_atr: float = Field(default=0.02, ge=0.0, le=5.0)
    max_penetration_atr: float = Field(default=1.5, gt=0.0, le=20.0)
    confirm_bars: int = Field(default=2, ge=0, le=50)
    max_close_location: float = Field(default=0.40, ge=0.0, le=1.0)
    require_close_back: bool = True
    min_pool_strength: float = Field(default=0.0, ge=0.0, le=20.0)

    # How far back an MSS may look for the sweep that originated its move.
    origin_lookback_bars: int = Field(default=20, ge=0, le=500)


class SMCRules(BaseModel):
    """Top-level rule set. Later phases add their sections here."""

    model_config = {"frozen": True}

    atr_period: int = Field(default=14, ge=2, le=200)
    swing: SwingConfig = SwingConfig()
    structure: StructureConfig = StructureConfig()
    displacement: DisplacementConfig = DisplacementConfig()
    breaks: BreakConfig = BreakConfig()
    sessions: SessionConfig = SessionConfig()
    liquidity: LiquidityConfig = LiquidityConfig()
    sweeps: SweepConfig = SweepConfig()
    fvg: FVGConfig = FVGConfig()
    order_blocks: OrderBlockConfig = OrderBlockConfig()
    dealing_range: RangeConfig = RangeConfig()
    regime: RegimeConfig = RegimeConfig()
    mtf: MTFConfig = MTFConfig()
    setups: SetupConfig = SetupConfig()

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
