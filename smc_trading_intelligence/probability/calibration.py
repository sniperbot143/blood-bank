"""Calibration: is a 70% actually right about 70% of the time?

An uncalibrated probability is worse than no probability -- it invites
position sizing off a number that does not mean what it says. Everything here
compares predictions to realised outcomes:

    reliability diagram   predicted vs realised, in bins
    Brier score           mean squared error of the probability
    log loss              punishes confident mistakes hard
    ECE                   average |predicted - realised| across bins

Two baselines are always reported next to the model: the base rate, and the
setup score / 100. If the probability engine cannot beat the base rate out of
sample, it is adding nothing and should say so.

Post-hoc correction (isotonic / Platt) is fitted on validation data only and
applied to out-of-sample data -- never fitted on the data it then corrects.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class CalibrationBin:
    lower: float
    upper: float
    count: int
    mean_predicted: float
    mean_actual: float

    @property
    def gap(self) -> float:
        return abs(self.mean_predicted - self.mean_actual)


@dataclass
class CalibrationReport:
    bins: list[CalibrationBin] = field(default_factory=list)
    brier: float = float("nan")
    log_loss: float = float("nan")
    ece: float = float("nan")
    base_rate: float = float("nan")
    brier_base_rate: float = float("nan")
    n: int = 0

    @property
    def beats_base_rate(self) -> bool:
        return (np.isfinite(self.brier) and np.isfinite(self.brier_base_rate)
                and self.brier < self.brier_base_rate)

    def summary(self) -> str:
        verdict = ("beats the base rate" if self.beats_base_rate
                   else "does NOT beat the base rate")
        return "\n".join([
            f"n            : {self.n:,}",
            f"base rate    : {self.base_rate:.3f}",
            f"Brier        : {self.brier:.4f}  (base rate {self.brier_base_rate:.4f})",
            f"log loss     : {self.log_loss:.4f}",
            f"ECE          : {self.ece:.4f}",
            f"verdict      : {verdict}",
        ])

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame([
            {"lower": b.lower, "upper": b.upper, "count": b.count,
             "predicted": b.mean_predicted, "actual": b.mean_actual, "gap": b.gap}
            for b in self.bins
        ])


def brier_score(predicted: np.ndarray, actual: np.ndarray) -> float:
    return float(np.mean((predicted - actual) ** 2)) if len(predicted) else float("nan")


def log_loss(predicted: np.ndarray, actual: np.ndarray, eps: float = 1e-12) -> float:
    if not len(predicted):
        return float("nan")
    p = np.clip(predicted, eps, 1 - eps)
    return float(-np.mean(actual * np.log(p) + (1 - actual) * np.log(1 - p)))


def calibration_report(
    predicted: np.ndarray | pd.Series,
    actual: np.ndarray | pd.Series,
    *,
    bins: int = 10,
) -> CalibrationReport:
    """Bin predictions and compare each bin's mean prediction to its outcome."""
    p = np.asarray(predicted, dtype="float64")
    y = np.asarray(actual, dtype="float64")
    mask = np.isfinite(p) & np.isfinite(y)
    p, y = p[mask], y[mask]
    if not len(p):
        return CalibrationReport()

    base_rate = float(y.mean())
    edges = np.linspace(0.0, 1.0, bins + 1)
    report = CalibrationReport(
        brier=brier_score(p, y), log_loss=log_loss(p, y), base_rate=base_rate,
        brier_base_rate=brier_score(np.full_like(p, base_rate), y), n=int(len(p)),
    )

    gaps, weights = [], []
    for i in range(bins):
        low, high = edges[i], edges[i + 1]
        inside = (p >= low) & (p < high if i < bins - 1 else p <= high)
        if not inside.any():
            continue
        bin_report = CalibrationBin(
            lower=float(low), upper=float(high), count=int(inside.sum()),
            mean_predicted=float(p[inside].mean()), mean_actual=float(y[inside].mean()),
        )
        report.bins.append(bin_report)
        gaps.append(bin_report.gap)
        weights.append(bin_report.count)

    report.ece = float(np.average(gaps, weights=weights)) if gaps else float("nan")
    return report


@dataclass
class IsotonicCalibrator:
    """Monotone post-hoc correction, fitted on validation data only."""

    x: np.ndarray = field(default_factory=lambda: np.empty(0))
    y: np.ndarray = field(default_factory=lambda: np.empty(0))
    fitted: bool = False

    def fit(self, predicted: np.ndarray, actual: np.ndarray) -> "IsotonicCalibrator":
        p = np.asarray(predicted, dtype="float64")
        a = np.asarray(actual, dtype="float64")
        mask = np.isfinite(p) & np.isfinite(a)
        p, a = p[mask], a[mask]
        if len(p) < 2:
            return self

        order = np.argsort(p)
        p, a = p[order], a[order]
        try:
            from sklearn.isotonic import IsotonicRegression

            model = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
            fitted = model.fit_transform(p, a)
        except Exception:
            fitted = _pool_adjacent_violators(a)

        self.x, self.y, self.fitted = p, np.clip(fitted, 0.0, 1.0), True
        return self

    def transform(self, predicted: np.ndarray | float) -> np.ndarray | float:
        if not self.fitted:
            return predicted
        scalar = np.isscalar(predicted)
        p = np.atleast_1d(np.asarray(predicted, dtype="float64"))
        out = np.interp(p, self.x, self.y, left=self.y[0], right=self.y[-1])
        return float(out[0]) if scalar else out


def _pool_adjacent_violators(values: np.ndarray) -> np.ndarray:
    """Minimal PAVA, so calibration works without scikit-learn installed."""
    y = values.astype("float64").copy()
    weights = np.ones_like(y)
    i = 0
    while i < len(y) - 1:
        if y[i] <= y[i + 1]:
            i += 1
            continue
        total = weights[i] + weights[i + 1]
        pooled = (y[i] * weights[i] + y[i + 1] * weights[i + 1]) / total
        y[i] = y[i + 1] = pooled
        weights[i] = weights[i + 1] = total
        i = max(i - 1, 0)
    return y
