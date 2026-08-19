"""Equal highs and equal lows -- the tightest liquidity there is.

Two or more swing highs printed at (nearly) the same price leave a shelf of
stop orders above them. The tolerance is ATR-scaled, so "equal" means the same
thing on gold and on EURUSD.

Clusters grow over time and that growth is recorded per member, so
`price_at(t)` / `member_count_at(t)` answer with only the members that had
confirmed by bar `t`. A third equal high found later never rewrites what the
pool looked like before it printed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from config.smc_rules import LiquidityConfig
from structure.swings import SwingKind, SwingPoint, SwingSeries


@dataclass(frozen=True)
class EqualMember:
    """One swing that belongs to an equal-level cluster."""

    price: float
    formed_at_index: int
    confirmed_at_index: int


@dataclass
class EqualLevelCluster:
    """A group of same-kind swings sitting within tolerance of each other."""

    kind: SwingKind
    members: list[EqualMember] = field(default_factory=list)
    tolerance: float = 0.0          # the ATR-scaled tolerance in price units

    # -- growth-aware accessors -------------------------------------------

    def members_at(self, index: int) -> list[EqualMember]:
        return [m for m in self.members if m.confirmed_at_index <= index]

    def member_count_at(self, index: int) -> int:
        return len(self.members_at(index))

    def price_at(self, index: int) -> float:
        """The defended level: the extreme of the members known by `index`."""
        known = [m.price for m in self.members_at(index)]
        if not known:
            return float("nan")
        return max(known) if self.kind is SwingKind.HIGH else min(known)

    def spread_at(self, index: int) -> float:
        known = [m.price for m in self.members_at(index)]
        if len(known) < 2:
            return 0.0
        return max(known) - min(known)

    def confirmed_at_index(self, min_members: int) -> int | None:
        """The bar the cluster became a pool -- when member `min_members` confirmed."""
        if len(self.members) < min_members:
            return None
        return self.members[min_members - 1].confirmed_at_index

    @property
    def first_formed_index(self) -> int:
        return self.members[0].formed_at_index

    @property
    def last_formed_index(self) -> int:
        return self.members[-1].formed_at_index

    def tightness_at(self, index: int) -> float:
        """1.0 = perfectly equal, 0.0 = at the edge of tolerance."""
        if self.tolerance <= 0:
            return 1.0
        return float(max(0.0, 1.0 - self.spread_at(index) / self.tolerance))


def find_equal_levels(
    swings: SwingSeries,
    atr_values: np.ndarray,
    config: LiquidityConfig,
) -> list[EqualLevelCluster]:
    """Cluster same-kind swings that print at the same level.

    Processed in confirmation order so a cluster can only ever grow forward in
    time. A candidate joins the most recent open cluster it matches; otherwise
    it opens a new one.
    """
    clusters: list[EqualLevelCluster] = []
    if not swings.swings:
        return clusters

    ordered: list[SwingPoint] = sorted(
        swings.swings, key=lambda s: (s.confirmed_at_index, s.formed_at_index)
    )
    open_clusters: dict[SwingKind, list[EqualLevelCluster]] = {
        SwingKind.HIGH: [], SwingKind.LOW: []
    }

    for swing in ordered:
        atr = (atr_values[swing.formed_at_index]
               if swing.formed_at_index < len(atr_values) else np.nan)
        if not np.isfinite(atr) or atr <= 0:
            continue  # cannot judge "equal" without a scale
        tolerance = config.equal_tolerance_atr * float(atr)

        joined = False
        for cluster in reversed(open_clusters[swing.kind]):
            if swing.formed_at_index - cluster.last_formed_index > config.equal_max_gap_bars:
                continue
            if abs(swing.price - cluster.price_at(swing.confirmed_at_index)) <= tolerance:
                cluster.members.append(
                    EqualMember(price=swing.price,
                                formed_at_index=swing.formed_at_index,
                                confirmed_at_index=swing.confirmed_at_index)
                )
                joined = True
                break

        if not joined:
            cluster = EqualLevelCluster(
                kind=swing.kind,
                members=[EqualMember(price=swing.price,
                                     formed_at_index=swing.formed_at_index,
                                     confirmed_at_index=swing.confirmed_at_index)],
                tolerance=tolerance,
            )
            clusters.append(cluster)
            open_clusters[swing.kind].append(cluster)

        # Drop clusters that can no longer be joined -- keeps the scan short.
        open_clusters[swing.kind] = [
            c for c in open_clusters[swing.kind]
            if swing.formed_at_index - c.last_formed_index <= config.equal_max_gap_bars
        ]

    return [c for c in clusters if len(c.members) >= config.equal_min_members]
