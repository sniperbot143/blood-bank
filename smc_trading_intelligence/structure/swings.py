"""Swing detection -- the first SMC layer, and the anti-repaint anchor.

Two indices are attached to every swing and the difference between them is
the whole point:

    formed_at_index      the bar the swing geometrically sits on
    confirmed_at_index   the first bar at which it could possibly be KNOWN
                         (formed + swing_right)

Nothing downstream may read a swing before its confirmation bar. Superseded
swings are never deleted either -- they carry `superseded_at_index`, so the
state "as known at bar t" is reproducible for any t. That is what makes
`SwingSeries.as_of(t)` equal to a fresh detection over `frame[:t+1]`, which
is asserted by the oracle test in tests/test_no_lookahead.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import pandas as pd

from common.indicators import rolling_median_causal, wilder_atr
from config.smc_rules import DEFAULT_RULES, SMCRules, SwingConfig, SwingMode


class SwingKind(str, Enum):
    HIGH = "HIGH"
    LOW = "LOW"

    @property
    def opposite(self) -> "SwingKind":
        return SwingKind.LOW if self is SwingKind.HIGH else SwingKind.HIGH


class RejectReason(str, Enum):
    ATR_FILTER = "ATR_FILTER"            # too small to be structural
    NO_ATR = "NO_ATR"                    # before ATR is seeded: cannot judge size
    NOT_EXTREME = "NOT_EXTREME"          # collapsed into a better same-kind swing
    SPANS_GAP = "SPANS_GAP"              # window crosses missing bars


@dataclass
class SwingPoint:
    """A confirmed swing point. Immutable except for supersession bookkeeping."""

    kind: SwingKind
    price: float
    formed_at_index: int
    formed_at: pd.Timestamp
    confirmed_at_index: int
    confirmed_at: pd.Timestamp
    left: int
    right: int
    atr_at_formation: float
    strength_atr: float
    spans_gap: bool = False
    symbol: str = ""
    timeframe: str = ""
    superseded_at_index: int | None = None
    superseded_by_index: int | None = None

    def is_known_at(self, index: int) -> bool:
        return self.confirmed_at_index <= index

    def is_live_at(self, index: int) -> bool:
        """Known, and not yet replaced by a more extreme same-kind swing."""
        if not self.is_known_at(index):
            return False
        return self.superseded_at_index is None or self.superseded_at_index > index

    def status_at(self, index: int) -> str:
        if index < self.formed_at_index:
            return "FUTURE"
        if index < self.confirmed_at_index:
            return "DEVELOPING"
        if self.superseded_at_index is not None and index >= self.superseded_at_index:
            return "SUPERSEDED"
        return "CONFIRMED"

    def as_dict(self) -> dict:
        return {
            "kind": self.kind.value,
            "price": self.price,
            "formed_at": self.formed_at,
            "formed_at_index": self.formed_at_index,
            "confirmed_at": self.confirmed_at,
            "confirmed_at_index": self.confirmed_at_index,
            "strength_atr": self.strength_atr,
            "atr_at_formation": self.atr_at_formation,
            "spans_gap": self.spans_gap,
            "superseded_at_index": self.superseded_at_index,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
        }


@dataclass
class RejectedSwing:
    """A candidate that did not become a swing, and why. Kept for diagnostics
    and for tuning `min_swing_atr` -- silently dropped candidates hide bugs."""

    kind: SwingKind
    price: float
    formed_at_index: int
    formed_at: pd.Timestamp
    reason: RejectReason
    detail: str = ""


@dataclass
class SwingSeries:
    """All swings detected over one frame, with time-travel accessors."""

    swings: list[SwingPoint] = field(default_factory=list)
    rejected: list[RejectedSwing] = field(default_factory=list)
    n_bars: int = 0
    symbol: str = ""
    timeframe: str = ""
    config: SwingConfig = field(default_factory=SwingConfig)

    # -- accessors ---------------------------------------------------------

    def as_of(self, index: int) -> list[SwingPoint]:
        """The live alternating chain exactly as it was known at bar `index`."""
        return [s for s in self.swings if s.is_live_at(index)]

    def known_at(self, index: int) -> list[SwingPoint]:
        """Everything confirmed by `index`, including superseded points."""
        return [s for s in self.swings if s.is_known_at(index)]

    @property
    def current(self) -> list[SwingPoint]:
        return self.as_of(self.n_bars - 1) if self.n_bars else []

    def last(self, kind: SwingKind | None = None, index: int | None = None) -> SwingPoint | None:
        at = self.n_bars - 1 if index is None else index
        for swing in reversed(self.as_of(at)):
            if kind is None or swing.kind is kind:
                return swing
        return None

    def highs(self, index: int | None = None) -> list[SwingPoint]:
        at = self.n_bars - 1 if index is None else index
        return [s for s in self.as_of(at) if s.kind is SwingKind.HIGH]

    def lows(self, index: int | None = None) -> list[SwingPoint]:
        at = self.n_bars - 1 if index is None else index
        return [s for s in self.as_of(at) if s.kind is SwingKind.LOW]

    def reject_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for r in self.rejected:
            counts[r.reason.value] = counts.get(r.reason.value, 0) + 1
        return counts

    def to_frame(self, index: int | None = None) -> pd.DataFrame:
        rows = [s.as_dict() for s in self.as_of(self.n_bars - 1 if index is None else index)]
        if not rows:
            return pd.DataFrame(columns=list(SwingPoint.__annotations__))
        return pd.DataFrame(rows).set_index("formed_at")

    def alternates(self, index: int | None = None) -> bool:
        """The live chain must strictly alternate HIGH/LOW; a violation is a bug."""
        chain = self.as_of(self.n_bars - 1 if index is None else index)
        return all(a.kind is not b.kind for a, b in zip(chain, chain[1:]))


# ---------------------------------------------------------------- detection

def _fractal_flags(values: np.ndarray, left: int, right: int, *, is_high: bool,
                   strict_left: bool) -> np.ndarray:
    """Vectorised fixed-window fractal test.

    FRACTAL is strict on the left and non-strict on the right, so a plateau of
    equal highs resolves to its FIRST bar -- the print that created the level.
    The later equal highs are not lost: they become equal-high liquidity in
    Phase 5. FIXED_LOOKBACK is non-strict on both sides (pure window extremum).
    """
    n = len(values)
    flags = np.zeros(n, dtype=bool)
    if n < left + right + 1:
        return flags

    idx = np.arange(left, n - right)
    ok = np.ones(len(idx), dtype=bool)
    centre = values[idx]

    for k in range(1, left + 1):
        other = values[idx - k]
        if is_high:
            ok &= centre > other if strict_left else centre >= other
        else:
            ok &= centre < other if strict_left else centre <= other

    for k in range(1, right + 1):
        other = values[idx + k]
        ok &= (centre >= other) if is_high else (centre <= other)

    flags[idx] = ok
    return flags


def _adaptive_windows(atr: np.ndarray, config: SwingConfig, n: int) -> tuple[np.ndarray, np.ndarray]:
    """Per-bar left/right windows scaled by ATR relative to its own recent median."""
    reference = rolling_median_causal(
        pd.Series(atr), window=config.adaptive_reference_window
    ).to_numpy(dtype="float64")

    with np.errstate(invalid="ignore", divide="ignore"):
        scale = np.where(reference > 0, atr / reference, 1.0)
    scale = np.nan_to_num(scale, nan=1.0, posinf=config.adaptive_max_scale)
    scale = np.clip(scale, config.adaptive_min_scale, config.adaptive_max_scale)

    lefts = np.maximum(1, np.rint(config.swing_left * scale)).astype(int)
    rights = np.maximum(1, np.rint(config.swing_right * scale)).astype(int)
    return lefts[:n], rights[:n]


def _adaptive_flags(values: np.ndarray, lefts: np.ndarray, rights: np.ndarray,
                    *, is_high: bool) -> np.ndarray:
    n = len(values)
    flags = np.zeros(n, dtype=bool)
    for i in range(n):
        left, right = int(lefts[i]), int(rights[i])
        if i - left < 0 or i + right >= n:
            continue
        centre = values[i]
        window_left = values[i - left:i]
        window_right = values[i + 1:i + right + 1]
        if is_high:
            if centre > window_left.max() and centre >= window_right.max():
                flags[i] = True
        else:
            if centre < window_left.min() and centre <= window_right.min():
                flags[i] = True
    return flags


def _require_closed(frame: pd.DataFrame) -> None:
    if "is_closed" in frame.columns and not bool(frame["is_closed"].all()):
        raise ValueError(
            "frame contains an unclosed bar. Pass closed_bars(frame) -- a forming "
            "candle must never produce structure."
        )


def detect_swings(
    frame: pd.DataFrame,
    rules: SMCRules = DEFAULT_RULES,
    *,
    symbol: str | None = None,
    timeframe: str | None = None,
    atr: pd.Series | None = None,
) -> SwingSeries:
    """Detect swing points over a canonical, closed-bar frame."""
    _require_closed(frame)
    config = rules.swing
    n = len(frame)

    symbol = symbol or (str(frame["symbol"].iloc[0]) if n and "symbol" in frame else "")
    timeframe = timeframe or (str(frame["timeframe"].iloc[0]) if n and "timeframe" in frame else "")
    series = SwingSeries(n_bars=n, symbol=symbol, timeframe=timeframe, config=config)
    if n == 0:
        return series

    highs = frame["high"].to_numpy(dtype="float64")
    lows = frame["low"].to_numpy(dtype="float64")
    atr_values = (atr if atr is not None else wilder_atr(frame, rules.atr_period)).to_numpy(dtype="float64")
    gap_before = (
        frame["gap_before"].to_numpy(dtype=bool)
        if "gap_before" in frame.columns
        else np.zeros(n, dtype=bool)
    )
    timestamps = frame.index

    if config.mode is SwingMode.ATR_ADAPTIVE:
        lefts, rights = _adaptive_windows(atr_values, config, n)
        high_flags = _adaptive_flags(highs, lefts, rights, is_high=True)
        low_flags = _adaptive_flags(lows, lefts, rights, is_high=False)
    else:
        strict_left = config.mode is SwingMode.FRACTAL
        lefts = np.full(n, config.swing_left, dtype=int)
        rights = np.full(n, config.swing_right, dtype=int)
        high_flags = _fractal_flags(highs, config.swing_left, config.swing_right,
                                    is_high=True, strict_left=strict_left)
        low_flags = _fractal_flags(lows, config.swing_left, config.swing_right,
                                   is_high=False, strict_left=strict_left)

    # Candidates are processed in the order they become KNOWN, never in the
    # order they formed -- with adaptive windows the two can differ.
    candidates: list[tuple[int, int, int, SwingKind]] = []
    for i in np.flatnonzero(high_flags):
        candidates.append((int(i) + int(rights[i]), int(i), 0, SwingKind.HIGH))
    for i in np.flatnonzero(low_flags):
        candidates.append((int(i) + int(rights[i]), int(i), 1, SwingKind.LOW))
    candidates.sort()

    chain: list[SwingPoint] = []

    for confirmed_at_index, i, _tie, kind in candidates:
        if confirmed_at_index >= n:
            continue  # cannot be known within this frame

        left, right = int(lefts[i]), int(rights[i])
        price = float(highs[i] if kind is SwingKind.HIGH else lows[i])
        atr_i = float(atr_values[i])
        lo = max(0, i - left)
        hi = min(n, i + right + 1)

        spans_gap = bool(gap_before[i - left + 1:i + right + 1].any()) if i - left + 1 < hi else False
        if spans_gap and config.reject_across_gaps:
            series.rejected.append(RejectedSwing(kind, price, i, timestamps[i],
                                                 RejectReason.SPANS_GAP, "window crosses missing bars"))
            continue

        if kind is SwingKind.HIGH:
            excursion = price - float(lows[lo:hi].min())
        else:
            excursion = float(highs[lo:hi].max()) - price
        strength = excursion / atr_i if atr_i and np.isfinite(atr_i) and atr_i > 0 else float("nan")

        # -- size filter: measured against the previous opposite swing -------
        if config.min_swing_atr > 0:
            if not np.isfinite(atr_i) or atr_i <= 0:
                series.rejected.append(RejectedSwing(kind, price, i, timestamps[i],
                                                     RejectReason.NO_ATR,
                                                     f"ATR not seeded (period={rules.atr_period})"))
                continue

            opposite = _last_opposite(chain, kind)
            distance = abs(price - opposite.price) if opposite is not None else excursion
            threshold = config.min_swing_atr * atr_i
            if distance < threshold:
                series.rejected.append(RejectedSwing(
                    kind, price, i, timestamps[i], RejectReason.ATR_FILTER,
                    f"{distance:.5f} < {threshold:.5f} ({config.min_swing_atr} x ATR)",
                ))
                continue

        candidate = SwingPoint(
            kind=kind,
            price=price,
            formed_at_index=i,
            formed_at=timestamps[i],
            confirmed_at_index=confirmed_at_index,
            confirmed_at=timestamps[confirmed_at_index],
            left=left,
            right=right,
            atr_at_formation=atr_i,
            strength_atr=strength,
            spans_gap=spans_gap,
            symbol=symbol,
            timeframe=timeframe,
        )

        # -- collapse consecutive same-kind swings ---------------------------
        if chain and chain[-1].kind is kind:
            previous = chain[-1]
            more_extreme = (
                price > previous.price if kind is SwingKind.HIGH else price < previous.price
            )
            if not more_extreme:
                series.rejected.append(RejectedSwing(
                    kind, price, i, timestamps[i], RejectReason.NOT_EXTREME,
                    f"does not exceed swing at index {previous.formed_at_index} "
                    f"({previous.price})",
                ))
                continue
            # The earlier swing stops being current from THIS bar onwards --
            # it is not erased, so history stays reproducible.
            previous.superseded_at_index = confirmed_at_index
            previous.superseded_by_index = i
            chain.pop()

        chain.append(candidate)
        series.swings.append(candidate)

    series.swings.sort(key=lambda s: (s.formed_at_index, s.kind.value))
    return series


def _last_opposite(chain: list[SwingPoint], kind: SwingKind) -> SwingPoint | None:
    for swing in reversed(chain):
        if swing.kind is kind.opposite:
            return swing
    return None
