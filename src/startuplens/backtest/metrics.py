"""Metric computation with pass/fail thresholds for backtest evaluation.

Every backtest run is evaluated against predefined thresholds.  The model
does not ship unless all "must pass" thresholds are met.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Metric result
# ---------------------------------------------------------------------------

@dataclass
class MetricResult:
    """Outcome of evaluating one metric against its threshold."""

    name: str
    value: float
    threshold: float
    passed: bool
    must_pass: bool
    failure_explanation: str = ""


@dataclass
class CalibrationBin:
    """One reliability bin from a calibration curve."""

    bin_index: int
    bin_lower: float
    bin_upper: float
    sample_size: int
    mean_pred: float | None
    observed_rate: float | None
    abs_error: float | None


# ---------------------------------------------------------------------------
# ECE (Expected Calibration Error)
# ---------------------------------------------------------------------------

def compute_ece(
    y_true: Sequence[int],
    y_pred_proba: Sequence[float],
    n_bins: int = 10,
) -> float:
    """Compute the Expected Calibration Error.

    Partitions predictions into *n_bins* equally-spaced bins by predicted
    probability and returns the weighted average absolute difference between
    predicted confidence and observed frequency.

    Parameters
    ----------
    y_true:
        Ground-truth binary labels (0 or 1).
    y_pred_proba:
        Predicted probabilities in [0, 1].
    n_bins:
        Number of calibration bins.

    Returns
    -------
    float
        ECE value in [0, 1].  Lower is better.
    """
    if len(y_true) != len(y_pred_proba):
        raise ValueError("y_true and y_pred_proba must have the same length")
    if len(y_true) == 0:
        return 0.0

    n = len(y_true)
    bin_boundaries = [i / n_bins for i in range(n_bins + 1)]
    ece = 0.0

    for b in range(n_bins):
        lo = bin_boundaries[b]
        hi = bin_boundaries[b + 1]

        # Collect indices in this bin (lower-inclusive, upper-exclusive except last bin)
        indices = [
            i for i, p in enumerate(y_pred_proba)
            if (lo <= p < hi) or (b == n_bins - 1 and p == hi)
        ]
        if not indices:
            continue

        avg_confidence = sum(y_pred_proba[i] for i in indices) / len(indices)
        avg_accuracy = sum(y_true[i] for i in indices) / len(indices)
        ece += (len(indices) / n) * abs(avg_accuracy - avg_confidence)

    return ece


def compute_calibration_bins(
    y_true: Sequence[int],
    y_pred_proba: Sequence[float],
    n_bins: int = 10,
) -> list[CalibrationBin]:
    """Compute fixed-bin calibration curve points used in evidence reports."""
    if len(y_true) != len(y_pred_proba):
        raise ValueError("y_true and y_pred_proba must have the same length")
    if n_bins <= 0:
        raise ValueError("n_bins must be positive")

    boundaries = [i / n_bins for i in range(n_bins + 1)]
    bins: list[CalibrationBin] = []
    for idx in range(n_bins):
        lo = boundaries[idx]
        hi = boundaries[idx + 1]
        indices = [
            i
            for i, p in enumerate(y_pred_proba)
            if (lo <= p < hi) or (idx == n_bins - 1 and p == hi)
        ]
        if not indices:
            bins.append(
                CalibrationBin(
                    bin_index=idx,
                    bin_lower=lo,
                    bin_upper=hi,
                    sample_size=0,
                    mean_pred=None,
                    observed_rate=None,
                    abs_error=None,
                ),
            )
            continue
        mean_pred = sum(y_pred_proba[i] for i in indices) / len(indices)
        observed = sum(y_true[i] for i in indices) / len(indices)
        bins.append(
            CalibrationBin(
                bin_index=idx,
                bin_lower=lo,
                bin_upper=hi,
                sample_size=len(indices),
                mean_pred=mean_pred,
                observed_rate=observed,
                abs_error=abs(observed - mean_pred),
            ),
        )
    return bins


# ---------------------------------------------------------------------------
# Threshold definitions
# ---------------------------------------------------------------------------

def _survival_auc(value: float) -> MetricResult:
    threshold = 0.65
    return MetricResult(
        name="Survival AUC",
        value=value,
        threshold=threshold,
        passed=value >= threshold,
        must_pass=True,
        failure_explanation=(
            "Model can't distinguish survivors from failures. "
            "Investigate feature engineering."
            if value < threshold else ""
        ),
    )


def _calibration_ece(value: float) -> MetricResult:
    threshold = 0.08
    return MetricResult(
        name="Calibration ECE",
        value=value,
        threshold=threshold,
        passed=value < threshold,
        must_pass=True,
        failure_explanation=(
            "Predicted probabilities unreliable. "
            "Apply Platt scaling or investigate distribution shift."
            if value >= threshold else ""
        ),
    )


def _portfolio_quality_vs_random(value: float) -> MetricResult:
    """Portfolio quality score ratio: model vs random.

    Uses revenue-growth-weighted outcomes to differentiate quality among
    survivors.  A ratio > 1.3 means the model picks companies whose
    post-raise trajectory is 30%+ better than random selection.
    """
    threshold = 1.3
    return MetricResult(
        name="Portfolio quality vs random",
        value=value,
        threshold=threshold,
        passed=value > threshold,
        must_pass=True,
        failure_explanation=(
            "Signal isn't worth the complexity. "
            "Model-selected portfolio must score 30%+ higher than random."
            if value <= threshold else ""
        ),
    )


def _portfolio_failure_rate_vs_random(value: float) -> MetricResult:
    threshold = 0.7
    return MetricResult(
        name="Portfolio failure rate vs random",
        value=value,
        threshold=threshold,
        passed=value < threshold,
        must_pass=True,
        failure_explanation=(
            "Model must reduce failure exposure by 30%+ vs random."
            if value >= threshold else ""
        ),
    )


def _claude_text_score_auc(value: float) -> MetricResult:
    threshold = 0.60
    return MetricResult(
        name="Claude text score AUC",
        value=value,
        threshold=threshold,
        passed=value >= threshold,
        must_pass=True,
        failure_explanation=(
            "Text analysis can't discriminate. "
            "Reduce text weight from 20% or investigate prompt quality."
            if value < threshold else ""
        ),
    )


def _progress_auc(value: float) -> MetricResult:
    threshold = 0.58
    return MetricResult(
        name="Progress AUC",
        value=value,
        threshold=threshold,
        passed=value >= threshold,
        must_pass=False,
        failure_explanation=(
            "18-month model adds no value. "
            "Drop it or simplify to a heuristic."
            if value < threshold else ""
        ),
    )


def _abstention_rate(value: float) -> MetricResult:
    """Model uncertainty rate: fraction of deals with P(fail) in [0.4, 0.6].

    A good model should be decisive — most deals should get a clear
    high or low probability, not cluster near 0.5.  An uncertainty rate
    above 40% means the model can't distinguish most deals.
    """
    threshold = 0.40
    return MetricResult(
        name="Model uncertainty rate",
        value=value,
        threshold=threshold,
        passed=value <= threshold,
        must_pass=False,
        failure_explanation=(
            "Model is uncertain on too many deals. "
            "Investigate feature quality or class balance."
            if value > threshold else ""
        ),
    )


def _sector_bias(max_sector_share: float) -> MetricResult:
    """Sector concentration in top-K model-ranked deals.

    Measures whether the model systematically favours one sector in its
    top-ranked deals (e.g. top 50), independent of portfolio policy size.
    A value above 0.50 means one sector dominates the model's picks.
    """
    threshold = 0.50
    return MetricResult(
        name="Top-K sector concentration",
        value=max_sector_share,
        threshold=threshold,
        passed=max_sector_share <= threshold,
        must_pass=False,
        failure_explanation=(
            "Model may be overfit to one sector's success pattern."
            if max_sector_share > threshold else ""
        ),
    )


# ---------------------------------------------------------------------------
# Aggregate evaluator
# ---------------------------------------------------------------------------

def evaluate_backtest(
    *,
    survival_auc: float,
    calibration_ece: float,
    portfolio_quality_vs_random: float,
    portfolio_failure_rate_vs_random: float,
    claude_text_score_auc: float,
    progress_auc: float = math.nan,
    model_uncertainty_rate: float = math.nan,
    top_k_sector_concentration: float = math.nan,
) -> list[MetricResult]:
    """Evaluate all backtest metrics against their thresholds.

    Returns a list of :class:`MetricResult` objects, one per metric.
    Metrics whose value is ``NaN`` are still included but automatically
    marked as *not passed* (with an appropriate explanation) so callers
    can inspect them.
    """
    results: list[MetricResult] = [
        _survival_auc(survival_auc),
        _calibration_ece(calibration_ece),
        _portfolio_quality_vs_random(portfolio_quality_vs_random),
        _portfolio_failure_rate_vs_random(portfolio_failure_rate_vs_random),
        _claude_text_score_auc(claude_text_score_auc),
        _progress_auc(progress_auc),
        _abstention_rate(model_uncertainty_rate),
        _sector_bias(top_k_sector_concentration),
    ]
    return results


def all_must_pass_met(results: list[MetricResult]) -> bool:
    """Return ``True`` only if every *must_pass* metric in *results* passed."""
    return all(r.passed for r in results if r.must_pass)
