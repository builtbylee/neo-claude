"""Regression tests for the backtest pipeline reliability.

These tests verify the failure modes that caused AUC=0.5 and ~100%
model uncertainty (all scores in the [40,60] band) in prior runs.

Root cause: the training_features_wide materialized view was never
refreshed after initial migration, so the backtest read stale/empty
feature rows.
"""

from __future__ import annotations

import math

import numpy as np

from startuplens.backtest.metrics import compute_ece, evaluate_backtest
from startuplens.model.train import (
    CATEGORICAL_FEATURES,
    FEATURE_COLUMNS,
    _build_feature_matrix,
    _build_target,
    score_deals,
    train_model,
)

# ---------------------------------------------------------------------------
# Helpers: synthetic labeled rows with real feature values
# ---------------------------------------------------------------------------

def _make_row(
    outcome: str = "trading",
    revenue: float | None = 50_000.0,
    funding_target: float | None = 100_000.0,
    employee_count: float | None = 5.0,
    company_age_months: float | None = 24.0,
    platform: str = "wefunder",
    country: str = "US",
    sector: str = "technology",
    **overrides,
) -> dict:
    """Create a synthetic feature row that looks like a real DB row."""
    row = {
        "entity_id": f"entity-{id(overrides)}",
        "company_id": f"company-{id(overrides)}",
        "campaign_date": "2020-06-15",
        "sector": sector,
        "platform": platform,
        "country": country,
        "company_age_months": company_age_months,
        "employee_count": employee_count,
        "revenue_at_raise": revenue,
        "pre_revenue": revenue is None or revenue == 0,
        "total_assets": 80_000.0,
        "total_debt": 20_000.0,
        "debt_to_asset_ratio": 0.25,
        "cash_position": 30_000.0,
        "funding_target": funding_target,
        "amount_raised": funding_target * 0.9 if funding_target else None,
        "overfunding_ratio": 0.9,
        "instrument_type": "equity",
        "outcome": outcome,
    }
    row.update(overrides)
    return row


def _make_labeled_dataset(
    n_trading: int = 80,
    n_failed: int = 40,
) -> list[dict]:
    """Create a balanced dataset of labeled rows with feature variation.

    Returns rows shuffled so that train/test splits contain both classes.
    """
    rng = np.random.RandomState(42)
    rows = []
    for i in range(n_trading):
        rows.append(_make_row(
            outcome="trading",
            revenue=float(rng.uniform(10_000, 500_000)),
            employee_count=float(rng.randint(2, 50)),
            company_age_months=float(rng.randint(6, 120)),
            funding_target=float(rng.uniform(50_000, 2_000_000)),
            entity_id=f"trading-{i}",
            company_id=f"company-trading-{i}",
        ))
    for i in range(n_failed):
        rows.append(_make_row(
            outcome="failed",
            revenue=float(rng.uniform(0, 50_000)),
            employee_count=float(rng.randint(1, 10)),
            company_age_months=float(rng.randint(1, 36)),
            funding_target=float(rng.uniform(10_000, 500_000)),
            entity_id=f"failed-{i}",
            company_id=f"company-failed-{i}",
        ))
    # Shuffle so train/test splits contain both classes
    rng.shuffle(rows)
    return rows


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFeatureMatrixConstruction:
    """Verify feature matrix doesn't degenerate to all-NaN."""

    def test_non_empty_features_produce_non_nan_matrix(self):
        """If rows have real values, the feature matrix must not be all NaN."""
        rows = _make_labeled_dataset(20, 10)
        x = _build_feature_matrix(rows, FEATURE_COLUMNS, CATEGORICAL_FEATURES)
        # At least 50% of cells should be non-NaN
        non_nan_frac = np.count_nonzero(~np.isnan(x)) / x.size
        assert non_nan_frac > 0.5, f"Feature matrix is {1 - non_nan_frac:.0%} NaN"

    def test_all_none_features_produce_all_nan_matrix(self):
        """If rows have all None values, matrix should be all NaN (the degenerate case)."""
        rows = [
            {"outcome": "trading", **{col: None for col in FEATURE_COLUMNS + CATEGORICAL_FEATURES}}
            for _ in range(10)
        ]
        x = _build_feature_matrix(rows, FEATURE_COLUMNS, CATEGORICAL_FEATURES)
        assert np.all(np.isnan(x)), "All-None rows should produce all-NaN matrix"

    def test_empty_rows_returns_empty_matrix(self):
        x = _build_feature_matrix([], FEATURE_COLUMNS, CATEGORICAL_FEATURES)
        assert x.shape == (0, len(FEATURE_COLUMNS) + len(CATEGORICAL_FEATURES))


class TestTrainModelWithRealFeatures:
    """The core regression test: training on real features must not degenerate."""

    def test_model_achieves_above_chance_auc(self):
        """With separable features, AUC must be > 0.5 (chance level).

        This is the regression test for the stale-matview bug.
        When the matview was empty, the model saw no features and
        predicted P(fail) ≈ 0.5 for all rows, yielding AUC = 0.5.
        """
        all_rows = _make_labeled_dataset(200, 100)
        # 70/30 split
        split = int(len(all_rows) * 0.7)
        train_rows = all_rows[:split]
        test_rows = all_rows[split:]

        trained = train_model(train_rows, test_rows, calibrate=False)

        assert trained.auc > 0.55, (
            f"AUC {trained.auc:.3f} is at or below chance level. "
            "This suggests the model received empty/constant features."
        )
        assert trained.n_train > 0
        assert trained.n_test > 0

    def test_scores_are_not_all_in_uncertain_band(self):
        """Model scores must not all cluster in [40, 60].

        When the matview was stale, all predictions were ~50 (P(fail) ≈ 0.5),
        causing 100% 'model uncertainty' in the backtest.
        """
        all_rows = _make_labeled_dataset(200, 100)
        split = int(len(all_rows) * 0.7)
        train_rows = all_rows[:split]
        test_rows = all_rows[split:]

        trained = train_model(train_rows, test_rows, calibrate=False)
        scores = score_deals(trained, test_rows)

        uncertain = sum(1 for s in scores if 40 <= s <= 60)
        uncertainty_rate = uncertain / len(scores)

        assert uncertainty_rate < 0.90, (
            f"Uncertainty rate {uncertainty_rate:.1%} is too high. "
            "Model is predicting ~50 for all deals (no feature signal)."
        )

    def test_model_with_no_features_degenerates(self):
        """Confirm that all-None features DO produce the degenerate case.

        This documents the exact failure mode so we know what to look for.
        """
        empty_rows = [
            {
                "entity_id": f"e-{i}",
                "company_id": f"c-{i}",
                "campaign_date": "2020-01-01",
                "outcome": "failed" if i % 3 == 0 else "trading",
                **{col: None for col in FEATURE_COLUMNS + CATEGORICAL_FEATURES},
            }
            for i in range(300)
        ]
        split = 200
        trained = train_model(empty_rows[:split], empty_rows[split:], calibrate=False)
        # With no features, AUC should be near 0.5 (chance)
        assert trained.auc < 0.60, (
            f"Expected degenerate AUC near 0.5, got {trained.auc:.3f}"
        )


class TestTargetVector:
    """Verify target vector construction."""

    def test_correct_labels(self):
        rows = [
            {"outcome": "failed"},
            {"outcome": "trading"},
            {"outcome": "failed"},
        ]
        y = _build_target(rows)
        np.testing.assert_array_equal(y, [1, 0, 1])


class TestEvaluateBacktestMetrics:
    """Verify the evaluate_backtest function handles edge cases."""

    def test_nan_metrics_are_flagged(self):
        """NaN values should result in failed metrics."""
        metrics = evaluate_backtest(
            survival_auc=math.nan,
            calibration_ece=math.nan,
            portfolio_quality_vs_random=math.nan,
            portfolio_failure_rate_vs_random=math.nan,
            claude_text_score_auc=math.nan,
            progress_auc=math.nan,
            model_uncertainty_rate=math.nan,
            top_k_sector_concentration=math.nan,
        )
        # All must-pass metrics should fail when NaN
        must_pass = [m for m in metrics if m.must_pass]
        for m in must_pass:
            assert not m.passed, f"Must-pass metric {m.name} should fail on NaN"

    def test_good_metrics_pass(self):
        metrics = evaluate_backtest(
            survival_auc=0.80,
            calibration_ece=0.03,
            portfolio_quality_vs_random=1.5,
            portfolio_failure_rate_vs_random=0.5,
            claude_text_score_auc=0.70,
            progress_auc=0.65,
            model_uncertainty_rate=0.30,
            top_k_sector_concentration=0.40,
        )
        must_pass = [m for m in metrics if m.must_pass]
        for m in must_pass:
            assert m.passed, f"Must-pass metric {m.name} should pass: {m.value}"


class TestComputeECE:
    """Verify ECE computation handles edge cases."""

    def test_perfect_calibration(self):
        """Perfect predictions should have ECE near 0."""
        y_true = [0, 0, 0, 0, 0, 1, 1, 1, 1, 1]
        y_prob = [0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0, 1.0]
        ece = compute_ece(y_true, y_prob)
        assert ece < 0.1

    def test_random_calibration(self):
        """Random predictions should have high ECE."""
        y_true = [0, 0, 0, 0, 0, 1, 1, 1, 1, 1]
        y_prob = [0.9, 0.9, 0.9, 0.9, 0.9, 0.1, 0.1, 0.1, 0.1, 0.1]
        ece = compute_ece(y_true, y_prob)
        assert ece > 0.5
