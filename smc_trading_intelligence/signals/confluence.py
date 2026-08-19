"""The setup score: how much confluence is present RIGHT NOW, out of 100.

This is not a probability and can never become one. It measures evidence in
the current picture; the probability measures what happened historically to
setups with that evidence. Two numbers, two code paths, on purpose
(docs/SMC_DEFINITIONS.md §30).

Every component scores continuously rather than 0/1 -- a displacement of 0.62
is worth more than one of 0.36 and less than one of 0.9 -- so the total moves
smoothly instead of jumping between plateaus.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from config.decision_config import DEFAULT_DECISION_CONFIG, DecisionConfig, ScoreWeights
from signals.setups import SetupCandidate


@dataclass
class ScoreComponent:
    name: str
    earned: float
    available: float
    detail: str = ""

    @property
    def fraction(self) -> float:
        return self.earned / self.available if self.available else 0.0


@dataclass
class SetupScore:
    """A 0-100 confluence score with every component itemised."""

    total: float
    components: list[ScoreComponent] = field(default_factory=list)

    @property
    def rounded(self) -> int:
        return int(round(self.total))

    def component(self, name: str) -> ScoreComponent | None:
        return next((c for c in self.components if c.name == name), None)

    def as_dict(self) -> dict:
        return {"setup_score": self.rounded,
                "components": {c.name: round(c.earned, 2) for c in self.components}}

    def describe(self) -> str:
        lines = [f"setup score: {self.rounded}/100"]
        for component in self.components:
            lines.append(f"  {component.name:<18} {component.earned:>5.1f} / "
                         f"{component.available:<5.1f} {component.detail}")
        return "\n".join(lines)


def _clamp01(value: float) -> float:
    if value is None or not np.isfinite(value):
        return 0.0
    return float(min(1.0, max(0.0, value)))


def score_setup(
    candidate: SetupCandidate,
    config: DecisionConfig = DEFAULT_DECISION_CONFIG,
) -> SetupScore:
    """Score one candidate's confluence from its own feature vector."""
    weights: ScoreWeights = config.weights
    values = candidate.features.values
    components: list[ScoreComponent] = []

    def add(name: str, fraction: float, available: float, detail: str = "") -> None:
        components.append(ScoreComponent(name=name, earned=available * _clamp01(fraction),
                                         available=available, detail=detail))

    # HTF bias: full credit only when every tracked timeframe agrees.
    agreement = values.get("htf_bias_agreement", "MIXED")
    count = values.get("htf_count", 0) or 0
    agreeing = values.get("htf_agreeing", 0) or 0
    htf_fraction = (agreeing / count) if count else 0.0
    if agreement == "OPPOSED":
        htf_fraction = 0.0
    add("htf_bias", htf_fraction, weights.htf_bias, f"{agreement} ({agreeing}/{count})")

    # Liquidity sweep: aligned, and sized.
    sweep_aligned = bool(values.get("sweep_aligned", 0))
    magnitude = values.get("sweep_magnitude_atr", float("nan"))
    rejection = values.get("sweep_rejection_atr", float("nan"))
    if sweep_aligned and np.isfinite(magnitude):
        size = _clamp01(magnitude / weights.sweep_full_magnitude_atr)
        reject = _clamp01(rejection) if np.isfinite(rejection) else 0.0
        sweep_fraction = 0.6 + 0.25 * size + 0.15 * reject
    else:
        sweep_fraction = 0.0
    add("liquidity_sweep", sweep_fraction, weights.liquidity_sweep,
        f"aligned={sweep_aligned} mag={magnitude:.2f}ATR" if np.isfinite(magnitude)
        else "no aligned sweep")

    # Structure shift: MSS full, aligned BOS partial.
    if values.get("mss_present") and values.get("structure_aligned"):
        mss_fraction = 1.0
    elif values.get("structure_aligned"):
        mss_fraction = 0.5
    else:
        mss_fraction = 0.0
    add("mss", mss_fraction, weights.mss, str(values.get("structure_event", "NONE")))

    displacement = values.get("displacement_score", float("nan"))
    add("displacement", displacement if np.isfinite(displacement) else 0.0,
        weights.displacement,
        f"{displacement:.2f}" if np.isfinite(displacement) else "none")

    # Order block: present, fresh, and not stale.
    if values.get("ob_present"):
        age = values.get("ob_age", 0) or 0
        freshness = 1.0 - _clamp01(age / weights.ob_max_age_bars)
        ob_fraction = 0.5 + 0.3 * freshness + 0.2 * (1.0 if values.get("ob_fresh") else 0.0)
    else:
        ob_fraction = 0.0
    add("order_block", ob_fraction, weights.order_block,
        f"age={values.get('ob_age')}" if values.get("ob_present") else "none")

    if values.get("fvg_present"):
        age = values.get("fvg_age", 0) or 0
        fill = values.get("fvg_fill", 0.0) or 0.0
        fvg_fraction = 0.5 + 0.3 * (1.0 - _clamp01(age / weights.fvg_max_age_bars)) \
            + 0.2 * (1.0 - _clamp01(fill))
    else:
        fvg_fraction = 0.0
    add("fvg", fvg_fraction, weights.fvg,
        f"fill={values.get('fvg_fill')}" if values.get("fvg_present") else "none")

    # Premium/discount: longs want discount, shorts want premium.
    zone = values.get("pd_zone", "NO_RANGE")
    bullish = candidate.bullish
    if zone == ("DISCOUNT" if bullish else "PREMIUM"):
        pd_fraction = 1.0
    elif zone == "EQUILIBRIUM":
        pd_fraction = 0.5
    else:
        pd_fraction = 0.0
    add("premium_discount", pd_fraction, weights.premium_discount, zone)

    session = values.get("session", "NONE")
    add("session", 1.0 if session in weights.preferred_sessions else 0.0,
        weights.session, str(session))

    rr = candidate.levels.rr1
    add("risk_reward", rr / weights.rr_full if np.isfinite(rr) else 0.0,
        weights.risk_reward, f"{rr:.2f}R" if np.isfinite(rr) else "n/a")

    return SetupScore(total=float(sum(c.earned for c in components)), components=components)
