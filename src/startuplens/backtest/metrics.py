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


def _portfolio_moic_vs_random(value: float) -> MetricResult:
    threshold = 1.3
    return MetricResult(
        name="Portfolio MOIC vs random",
        value=value,
        threshold=threshold,
        passed=value > threshold,
        must_pass=True,
        failure_explanation=(
            "Signal isn't worth the complexity. "
            "Model-selected portfolio must return 30%+ more than random."
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
    lo, hi = 0.10, 0.40
    passed = lo <= value <= hi
    return MetricResult(
        name="Abstention rate",
        value=value,
        threshold=lo,  # store lower bound as the primary threshold
        passed=passed,
        must_pass=False,
        failure_explanation=(
            "Below 10%: gates too loose."
            if value < lo
            else "Above 40%: gates too strict, tool unusable."
            if value > hi
            else ""
        ),
    )


def _sector_bias(max_sector_share: float) -> MetricResult:
    threshold = 0.50
    return MetricResult(
        name="Sector bias",
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
    portfolio_moic_vs_random: float,
    portfolio_failure_rate_vs_random: float,
    claude_text_score_auc: float,
    progress_auc: float = math.nan,
    abstention_rate: float = math.nan,
    max_sector_share: float = math.nan,
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
        _portfolio_moic_vs_random(portfolio_moic_vs_random),
        _portfolio_failure_rate_vs_random(portfolio_failure_rate_vs_random),
        _claude_text_score_auc(claude_text_score_auc),
        _progress_auc(progress_auc),
        _abstention_rate(abstention_rate),
        _sector_bias(max_sector_share),
    ]
    return results


def all_must_pass_met(results: list[MetricResult]) -> bool:
    """Return ``True`` only if every *must_pass* metric in *results* passed."""
    return all(r.passed for r in results if r.must_pass)
