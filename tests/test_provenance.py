"""Tests for backtest provenance logging."""

from datetime import date
from unittest.mock import MagicMock, patch

from startuplens.backtest.provenance import (
    compare_runs,
    get_backtest_run,
    get_latest_runs,
    get_passing_runs,
    log_backtest_run,
)


class TestLogBacktestRun:
    def test_returns_run_id(self):
        conn = MagicMock()
        with patch(
            "startuplens.backtest.provenance.execute_query",
            return_value=[{"id": 42}],
        ):
            run_id = log_backtest_run(
                conn,
                model_family="UK_Seed",
                data_snapshot_date=date(2025, 1, 15),
                train_window="2016-2022",
                test_window="2023-2025",
                features_active=["funding_target", "company_age_months"],
                metrics={"survival_auc": 0.72, "ece": 0.05},
                baselines={"random": {"auc": 0.50}},
                pass_fail={"survival_auc": {"value": 0.72, "threshold": 0.65, "passed": True}},
                all_passed=True,
            )
        assert run_id == 42

    def test_passes_all_fields_to_query(self):
        conn = MagicMock()
        with patch(
            "startuplens.backtest.provenance.execute_query",
            return_value=[{"id": 1}],
        ) as mock:
            log_backtest_run(
                conn,
                model_family="US_Seed",
                model_version_id=5,
                data_snapshot_date=date(2025, 2, 1),
                train_window="2016-2021",
                test_window="2022",
                features_active=["a", "b"],
                alt_data_signals=["wayback", "news"],
                metrics={"auc": 0.7},
                baselines={},
                pass_fail={},
                all_passed=False,
                notes="test run",
            )
        args = mock.call_args[0]
        params = args[2]
        assert params[0] == "US_Seed"  # model_family
        assert params[1] == 5  # model_version_id
        assert params[10] is False  # all_passed
        assert params[11] == "test run"  # notes

    def test_alt_data_defaults_to_empty_list(self):
        conn = MagicMock()
        with patch(
            "startuplens.backtest.provenance.execute_query",
            return_value=[{"id": 1}],
        ) as mock:
            log_backtest_run(
                conn,
                model_family="UK_Seed",
                data_snapshot_date=date(2025, 1, 1),
                train_window="2016-2022",
                test_window="2023-2025",
                features_active=[],
                metrics={},
                baselines={},
                pass_fail={},
                all_passed=True,
            )
        params = mock.call_args[0][2]
        assert params[6] == "[]"  # alt_data_signals serialized


class TestGetBacktestRun:
    def test_returns_row_when_found(self):
        conn = MagicMock()
        expected = {"id": 1, "model_family": "UK_Seed", "all_passed": True}
        with patch(
            "startuplens.backtest.provenance.execute_query",
            return_value=[expected],
        ):
            result = get_backtest_run(conn, 1)
        assert result == expected

    def test_returns_none_when_not_found(self):
        conn = MagicMock()
        with patch("startuplens.backtest.provenance.execute_query", return_value=[]):
            result = get_backtest_run(conn, 999)
        assert result is None


class TestGetLatestRuns:
    def test_returns_runs(self):
        conn = MagicMock()
        expected = [{"id": 3}, {"id": 2}, {"id": 1}]
        with patch(
            "startuplens.backtest.provenance.execute_query",
            return_value=expected,
        ):
            result = get_latest_runs(conn, limit=3)
        assert len(result) == 3

    def test_filters_by_model_family(self):
        conn = MagicMock()
        with patch(
            "startuplens.backtest.provenance.execute_query",
            return_value=[{"id": 1}],
        ) as mock:
            get_latest_runs(conn, model_family="UK_Seed", limit=5)
        query = mock.call_args[0][1]
        assert "model_family = %s" in query

    def test_no_family_filter(self):
        conn = MagicMock()
        with patch(
            "startuplens.backtest.provenance.execute_query",
            return_value=[],
        ) as mock:
            get_latest_runs(conn, limit=5)
        query = mock.call_args[0][1]
        assert "model_family" not in query


class TestGetPassingRuns:
    def test_filters_passing_only(self):
        conn = MagicMock()
        with patch(
            "startuplens.backtest.provenance.execute_query",
            return_value=[{"id": 1, "all_passed": True}],
        ) as mock:
            result = get_passing_runs(conn)
        query = mock.call_args[0][1]
        assert "all_passed = true" in query
        assert len(result) == 1

    def test_filters_by_family(self):
        conn = MagicMock()
        with patch(
            "startuplens.backtest.provenance.execute_query",
            return_value=[],
        ) as mock:
            get_passing_runs(conn, model_family="US_Seed")
        query = mock.call_args[0][1]
        assert "model_family = %s" in query
        assert "all_passed = true" in query


class TestCompareRuns:
    def test_computes_deltas(self):
        run_a = {"id": 1, "metrics": {"auc": 0.65, "ece": 0.08}, "all_passed": False}
        run_b = {"id": 2, "metrics": {"auc": 0.72, "ece": 0.05}, "all_passed": True}
        conn = MagicMock()
        with patch(
            "startuplens.backtest.provenance.execute_query",
            side_effect=[[run_a], [run_b]],
        ):
            result = compare_runs(conn, 1, 2)
        assert result["run_a_passed"] is False
        assert result["run_b_passed"] is True
        assert result["metrics"]["auc"]["delta"] == pytest.approx(0.07)
        assert result["metrics"]["ece"]["delta"] == pytest.approx(-0.03)

    def test_missing_run_returns_error(self):
        conn = MagicMock()
        with patch(
            "startuplens.backtest.provenance.execute_query",
            side_effect=[[], [{"id": 2, "metrics": {}, "all_passed": True}]],
        ):
            result = compare_runs(conn, 999, 2)
        assert "error" in result

    def test_handles_non_numeric_metrics(self):
        run_a = {"id": 1, "metrics": {"label": "v1"}, "all_passed": True}
        run_b = {"id": 2, "metrics": {"label": "v2"}, "all_passed": True}
        conn = MagicMock()
        with patch(
            "startuplens.backtest.provenance.execute_query",
            side_effect=[[run_a], [run_b]],
        ):
            result = compare_runs(conn, 1, 2)
        assert result["metrics"]["label"]["delta"] is None

    def test_handles_disjoint_metric_keys(self):
        run_a = {"id": 1, "metrics": {"auc": 0.7}, "all_passed": True}
        run_b = {"id": 2, "metrics": {"ece": 0.05}, "all_passed": True}
        conn = MagicMock()
        with patch(
            "startuplens.backtest.provenance.execute_query",
            side_effect=[[run_a], [run_b]],
        ):
            result = compare_runs(conn, 1, 2)
        assert "auc" in result["metrics"]
        assert "ece" in result["metrics"]
        assert result["metrics"]["auc"]["run_b"] is None


import pytest
