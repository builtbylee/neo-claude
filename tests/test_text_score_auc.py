"""Tests for Claude text score AUC computation."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from startuplens.backtest.text_score_auc import (
    compute_claude_text_auc,
    compute_dimension_aucs,
)


def _mock_conn_with_rows(rows: list[dict]) -> MagicMock:
    """Create a mock connection where execute_query returns the given rows."""
    conn = MagicMock()
    return conn


class TestComputeClaudeTextAuc:
    def test_perfect_discrimination(self):
        """High scores for survivors, low for failures → AUC ≈ 1.0."""
        rows = [
            {"text_quality_score": 80, "outcome": "trading"},
            {"text_quality_score": 85, "outcome": "trading"},
            {"text_quality_score": 90, "outcome": "exited"},
            {"text_quality_score": 75, "outcome": "trading"},
            {"text_quality_score": 70, "outcome": "trading"},
            {"text_quality_score": 20, "outcome": "failed"},
            {"text_quality_score": 15, "outcome": "failed"},
            {"text_quality_score": 10, "outcome": "failed"},
            {"text_quality_score": 25, "outcome": "failed"},
            {"text_quality_score": 30, "outcome": "failed"},
        ]
        conn = MagicMock()
        with patch("startuplens.db.execute_query", return_value=rows):
            auc = compute_claude_text_auc(conn)
        assert auc >= 0.95

    def test_random_scores(self):
        """Random scores → AUC ≈ 0.5."""
        import random

        random.seed(42)
        rows = []
        for _ in range(50):
            score = random.randint(0, 100)
            outcome = random.choice(["trading", "failed"])
            rows.append({"text_quality_score": score, "outcome": outcome})

        conn = MagicMock()
        with patch("startuplens.db.execute_query", return_value=rows):
            auc = compute_claude_text_auc(conn)
        assert 0.3 <= auc <= 0.7  # Should be near 0.5

    def test_insufficient_data(self):
        """Fewer than 10 rows → returns 0.0."""
        rows = [
            {"text_quality_score": 80, "outcome": "trading"},
            {"text_quality_score": 20, "outcome": "failed"},
        ]
        conn = MagicMock()
        with patch("startuplens.db.execute_query", return_value=rows):
            auc = compute_claude_text_auc(conn)
        assert auc == 0.0

    def test_single_class(self):
        """All same outcome → returns 0.0."""
        rows = [
            {"text_quality_score": s, "outcome": "trading"}
            for s in range(10, 110, 10)
        ]
        conn = MagicMock()
        with patch("startuplens.db.execute_query", return_value=rows):
            auc = compute_claude_text_auc(conn)
        assert auc == 0.0

    def test_empty_data(self):
        conn = MagicMock()
        with patch("startuplens.db.execute_query", return_value=[]):
            auc = compute_claude_text_auc(conn)
        assert auc == 0.0


class TestComputeDimensionAucs:
    def test_returns_all_dimensions(self):
        rows = [
            {
                "clarity": 80, "claims_plausibility": 70,
                "problem_specificity": 60, "differentiation_depth": 50,
                "founder_domain_signal": 40, "risk_honesty": 90,
                "business_model_clarity": 75, "text_quality_score": 65,
                "outcome": "trading",
            },
            {
                "clarity": 20, "claims_plausibility": 30,
                "problem_specificity": 40, "differentiation_depth": 50,
                "founder_domain_signal": 60, "risk_honesty": 10,
                "business_model_clarity": 25, "text_quality_score": 35,
                "outcome": "failed",
            },
        ] * 6  # 12 rows total

        conn = MagicMock()
        with patch("startuplens.db.execute_query", return_value=rows):
            aucs = compute_dimension_aucs(conn)

        assert len(aucs) == 8
        assert "clarity" in aucs
        assert "text_quality_score" in aucs
        assert all(0 <= v <= 1 for v in aucs.values())

    def test_insufficient_data(self):
        conn = MagicMock()
        with patch("startuplens.db.execute_query", return_value=[]):
            aucs = compute_dimension_aucs(conn)
        assert all(v == 0.0 for v in aucs.values())
