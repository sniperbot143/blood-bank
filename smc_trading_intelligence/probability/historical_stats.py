"""Finding comparable setups: tiered exact matching with back-off.

No magic distance metric. Five tiers, most specific first
(docs/PROBABILITY_METHODOLOGY.md §3):

    T1  symbol + timeframe + setup_type + direction + regime + session + zone
    T2  symbol + timeframe + setup_type + direction + regime
    T3  symbol + timeframe + setup_type + direction
    T4  symbol + timeframe + family    + direction
    T5  timeframe + family + direction              (across symbols)

Start at T1; if fewer than `min_samples` usable rows, drop a tier. The tier
that was actually used is reported with the estimate, because a T4 answer is
honest and a T1 answer built from seven rows is not.

Every query is filtered by `resolved_before`, so a trade whose outcome was
still unknown at signal time can never contribute.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from config.probability_config import DEFAULT_PROBABILITY_CONFIG, ProbabilityConfig
from database.models import SetupStore


@dataclass(frozen=True)
class SimilarityKey:
    """What we are looking for comparables of."""

    symbol: str
    timeframe: str
    setup_type: str
    family: str
    direction: str
    regime_key: str | None = None
    session: str | None = None
    pd_zone: str | None = None

    @classmethod
    def from_candidate(cls, candidate) -> "SimilarityKey":
        values = candidate.features.values
        return cls(
            symbol=candidate.symbol, timeframe=candidate.timeframe,
            setup_type=candidate.setup_type, family=candidate.family.value,
            direction=candidate.direction,
            regime_key=str(values.get("regime_key") or ""),
            session=str(values.get("session") or ""),
            pd_zone=str(values.get("pd_zone") or ""),
        )


TIERS = ("T1", "T2", "T3", "T4", "T5")


def _filters_for(tier: str, key: SimilarityKey) -> dict:
    if tier == "T1":
        return dict(symbol=key.symbol, timeframe=key.timeframe, setup_type=key.setup_type,
                    direction=key.direction, regime_key=key.regime_key,
                    session=key.session, pd_zone=key.pd_zone)
    if tier == "T2":
        return dict(symbol=key.symbol, timeframe=key.timeframe, setup_type=key.setup_type,
                    direction=key.direction, regime_key=key.regime_key)
    if tier == "T3":
        return dict(symbol=key.symbol, timeframe=key.timeframe, setup_type=key.setup_type,
                    direction=key.direction)
    if tier == "T4":
        return dict(symbol=key.symbol, timeframe=key.timeframe, family=key.family,
                    direction=key.direction)
    return dict(timeframe=key.timeframe, family=key.family, direction=key.direction)


@dataclass
class ComparableSet:
    """The rows a probability will be built from, and where they came from."""

    rows: pd.DataFrame
    tier: str
    tier_counts: dict[str, int]
    key: SimilarityKey
    as_of: pd.Timestamp | None

    @property
    def size(self) -> int:
        return len(self.rows)

    @property
    def is_sufficient(self) -> bool:
        return not self.rows.empty


def find_comparables(
    store: SetupStore,
    key: SimilarityKey,
    *,
    as_of: pd.Timestamp | None = None,
    config: ProbabilityConfig = DEFAULT_PROBABILITY_CONFIG,
    tiers: tuple[str, ...] = TIERS,
) -> ComparableSet:
    """Walk the tiers until one has enough resolved, non-overlapping rows."""
    counts: dict[str, int] = {}
    chosen: pd.DataFrame | None = None
    chosen_tier = tiers[-1]

    for tier in tiers:
        rows = store.query(resolved_before=as_of, **_filters_for(tier, key))
        rows = rows[rows["outcome"] != "NO_FILL"] if "outcome" in rows.columns else rows
        counts[tier] = len(rows)
        if chosen is None and len(rows) >= config.min_samples:
            chosen, chosen_tier = rows, tier

    if chosen is None:
        # Nothing met the bar: report the broadest evidence available, and let
        # the reliability tier say how little it is worth.
        best_tier = max(counts, key=lambda t: counts[t]) if counts else tiers[-1]
        chosen = store.query(resolved_before=as_of, **_filters_for(best_tier, key))
        if "outcome" in chosen.columns:
            chosen = chosen[chosen["outcome"] != "NO_FILL"]
        chosen_tier = best_tier

    return ComparableSet(rows=chosen.reset_index(drop=True), tier=chosen_tier,
                         tier_counts=counts, key=key, as_of=as_of)


def outcome_flags(rows: pd.DataFrame) -> dict[str, pd.Series]:
    """Boolean series for each thing we estimate a probability of."""
    outcome = rows["outcome"] if "outcome" in rows.columns else pd.Series(dtype=str)
    r = rows["r_multiple"] if "r_multiple" in rows.columns else pd.Series(dtype=float)
    return {
        "tp1": outcome.isin(["TP1_FIRST", "TP2_FIRST", "TP3_FIRST"]),
        "tp2": outcome.isin(["TP2_FIRST", "TP3_FIRST"]),
        "positive_r": r > 0,
    }
