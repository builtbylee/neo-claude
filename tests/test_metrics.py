"""Tests for backtest metric computation and threshold logic."""

from __future__ import annotations

import pytest

from startuplens.backtest.metrics import (
    all_must_pass_met,
    compute_calibration_bins,
    compute_ece,
    evaluate_backtest,
)

# ------------------------------------------------------------------
# ECE computation
# ------------------------------------------------------------------


class TestComputeECE:
    """Verify ECE (Expected Calibration Error) calculation."""

    def test_perfectly_calibrated(self):
        """If predictions equal actual frequencies, ECE should be ~0."""
        # 10 samples: predict 0.5, half are 1 and half are 0
        y_true = [1, 0, 1, 0, 1, 0, 1, 0, 1, 0]
        y_pred = [0.5] * 10
        ece = compute_ece(y_true, y_pred, n_bins=5)
        assert ece == pytest.approx(0.0, abs=0.01)

    def test_completely_miscalibrated(self):
        """Predict 1.0 for all but none are positive -> ECE should be high."""
        y_true = [0, 0, 0, 0, 0]
        y_pred = [0.99, 0.99, 0.99, 0.99, 0.99]
        ece = compute_ece(y_true, y_pred, n_bins=10)
        assert ece > 0.8

    def test_empty_input(self):
        assert compute_ece([], [], n_bins=10) == 0.0

    def test_length_mismatch_raises(self):
        with pytest.raises(ValueError, match="same length"):
            compute_ece([1, 0], [0.5], n_bins=10)

    def test_single_sample(self):
        ece = compute_ece([1], [0.7], n_bins=10)
        # |accuracy(1.0) - confidence(0.7)| = 0.3, weighted by 1/1
        assert ece == pytest.approx(0.3, abs=0.05)


class TestCalibrationBins:
    """Verify fixed-bin calibration curve generation."""

    def test_compute_calibration_bins_shape(self):
        bins = compute_calibration_bins(
            y_true=[0, 0, 1, 1, 1],
            y_pred_proba=[0.1, 0.2, 0.7, 0.8, 0.9],
            n_bins=5,
        )
        assert len(bins) == 5
        assert bins[0].bin_index == 0
        assert bins[-1].bin_index == 4

    def test_compute_calibration_bins_empty_bin(self):
        bins = compute_calibration_bins(
            y_true=[1, 1],
            y_pred_proba=[0.95, 0.96],
            n_bins=4,
        )
        # First bins should be empty and carry null means/rates.
        assert bins[0].sample_size == 0
        assert bins[0].mean_pred is None
        assert bins[0].observed_rate is None
        assert bins[0].abs_error is None

    def test_compute_calibration_bins_errors(self):
        with pytest.raises(ValueError, match="same length"):
            compute_calibration_bins([1], [0.2, 0.3], n_bins=5)
        with pytest.raises(ValueError, match="positive"):
            compute_calibration_bins([1], [0.2], n_bins=0)


# ------------------------------------------------------------------
# Threshold pass/fail logic
# ------------------------------------------------------------------


class TestThresholds:
    """Verify that each metric's pass/fail boundary is correct."""

    def _base_kwargs(self, **overrides):
        """Return a valid set of kwargs that passes all must-pass metrics."""
        defaults = dict(
            survival_auc=0.70,
            calibration_ece=0.05,
            portfolio_quality_vs_random=1.5,
            portfolio_failure_rate_vs_random=0.5,
            claude_text_score_auc=0.65,
            progress_auc=0.60,
            model_uncertainty_rate=0.20,
            top_k_sector_concentration=0.30,
        )
        defaults.update(overrides)
        return defaults

    def test_survival_auc_boundary_fail(self):
        results = evaluate_backtest(**self._base_kwargs(survival_auc=0.64))
        survival = next(r for r in results if r.name == "Survival AUC")
        assert not survival.passed
        assert survival.must_pass is True

    def test_survival_auc_boundary_pass(self):
        results = evaluate_backtest(**self._base_kwargs(survival_auc=0.65))
        survival = next(r for r in results if r.name == "Survival AUC")
        assert survival.passed

    def test_calibration_ece_boundary_fail(self):
        results = evaluate_backtest(**self._base_kwargs(calibration_ece=0.08))
        ece = next(r for r in results if r.name == "Calibration ECE")
        assert not ece.passed
        assert ece.must_pass is True

    def test_calibration_ece_boundary_pass(self):
        results = evaluate_backtest(**self._base_kwargs(calibration_ece=0.079))
        ece = next(r for r in results if r.name == "Calibration ECE")
        assert ece.passed

    def test_portfolio_quality_boundary_fail(self):
        results = evaluate_backtest(**self._base_kwargs(portfolio_quality_vs_random=1.3))
        moic = next(r for r in results if r.name == "Portfolio quality vs random")
        assert not moic.passed
        assert moic.must_pass is True

    def test_portfolio_quality_boundary_pass(self):
        results = evaluate_backtest(**self._base_kwargs(portfolio_quality_vs_random=1.31))
        moic = next(r for r in results if r.name == "Portfolio quality vs random")
        assert moic.passed

    def test_failure_rate_boundary_fail(self):
        results = evaluate_backtest(**self._base_kwargs(portfolio_failure_rate_vs_random=0.7))
        fr = next(r for r in results if r.name == "Portfolio failure rate vs random")
        assert not fr.passed
        assert fr.must_pass is True

    def test_failure_rate_boundary_pass(self):
        results = evaluate_backtest(**self._base_kwargs(portfolio_failure_rate_vs_random=0.69))
        fr = next(r for r in results if r.name == "Portfolio failure rate vs random")
        assert fr.passed

    def test_claude_text_auc_boundary_fail(self):
        results = evaluate_backtest(**self._base_kwargs(claude_text_score_auc=0.59))
        txt = next(r for r in results if r.name == "Claude text score AUC")
        assert not txt.passed
        assert txt.must_pass is True

    def test_claude_text_auc_boundary_pass(self):
        results = evaluate_backtest(**self._base_kwargs(claude_text_score_auc=0.60))
        txt = next(r for r in results if r.name == "Claude text score AUC")
        assert txt.passed

    def test_progress_auc_advisory(self):
        results = evaluate_backtest(**self._base_kwargs(progress_auc=0.57))
        prog = next(r for r in results if r.name == "Progress AUC")
        assert not prog.passed
        assert prog.must_pass is False

    def test_uncertainty_rate_pass(self):
        results = evaluate_backtest(**self._base_kwargs(model_uncertainty_rate=0.15))
        ur = next(r for r in results if r.name == "Model uncertainty rate")
        assert ur.passed
        assert ur.must_pass is False

    def test_uncertainty_rate_fail(self):
        results = evaluate_backtest(**self._base_kwargs(model_uncertainty_rate=0.45))
        ur = next(r for r in results if r.name == "Model uncertainty rate")
        assert not ur.passed

    def test_uncertainty_rate_boundary(self):
        results = evaluate_backtest(**self._base_kwargs(model_uncertainty_rate=0.40))
        ur = next(r for r in results if r.name == "Model uncertainty rate")
        assert ur.passed

    def test_top_k_sector_concentration_fail(self):
        results = evaluate_backtest(**self._base_kwargs(top_k_sector_concentration=0.55))
        sc = next(r for r in results if r.name == "Top-K sector concentration")
        assert not sc.passed
        assert sc.must_pass is False

    def test_top_k_sector_concentration_pass(self):
        results = evaluate_backtest(**self._base_kwargs(top_k_sector_concentration=0.40))
        sc = next(r for r in results if r.name == "Top-K sector concentration")
        assert sc.passed


# ------------------------------------------------------------------
# all_must_pass_met
# ------------------------------------------------------------------


class TestAllMustPassMet:
    """Verify the aggregate pass/fail gate."""

    def test_all_passing(self):
        results = evaluate_backtest(
            survival_auc=0.70,
            calibration_ece=0.05,
            portfolio_quality_vs_random=1.5,
            portfolio_failure_rate_vs_random=0.5,
            claude_text_score_auc=0.65,
        )
        assert all_must_pass_met(results)

    def test_one_must_pass_failing(self):
        results = evaluate_backtest(
            survival_auc=0.50,  # fails
            calibration_ece=0.05,
            portfolio_quality_vs_random=1.5,
            portfolio_failure_rate_vs_random=0.5,
            claude_text_score_auc=0.65,
        )
        assert not all_must_pass_met(results)

    def test_advisory_failing_does_not_block(self):
        results = evaluate_backtest(
            survival_auc=0.70,
            calibration_ece=0.05,
            portfolio_quality_vs_random=1.5,
            portfolio_failure_rate_vs_random=0.5,
            claude_text_score_auc=0.65,
            progress_auc=0.40,                  # advisory — fails
            model_uncertainty_rate=0.50,         # advisory — fails
            top_k_sector_concentration=0.80,     # advisory — fails
        )
        assert all_must_pass_met(results)

    def test_returns_eight_metrics(self):
        results = evaluate_backtest(
            survival_auc=0.70,
            calibration_ece=0.05,
            portfolio_quality_vs_random=1.5,
            portfolio_failure_rate_vs_random=0.5,
            claude_text_score_auc=0.65,
            progress_auc=0.60,
            model_uncertainty_rate=0.20,
            top_k_sector_concentration=0.30,
        )
        assert len(results) == 8
