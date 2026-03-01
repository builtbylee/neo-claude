"""Tests for run_backtest.py growth enrichment (temporal safety)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Allow importing the scripts/ module
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from run_backtest import _enrich_growth_features  # noqa: E402


class TestEnrichGrowthFeaturesPerRow:
    """Verify growth enrichment uses per-row campaign_date, not a shared cutoff."""

    @patch("run_backtest.execute_query")
    def test_same_company_different_dates_get_distinct_features(
        self, mock_eq: MagicMock,
    ):
        """Two rows for the same company at different dates must receive
        different growth features — proving no within-window look-ahead."""
        rows = [
            {"company_id": "c1", "campaign_date": "2020-01-15"},
            {"company_id": "c1", "campaign_date": "2021-06-15"},
        ]
        mock_eq.return_value = [
            {
                "company_id": "c1",
                "campaign_date": "2020-01-15",
                "revenue_growth_yoy": 0.10,
                "asset_growth_yoy": 0.05,
                "cash_growth_yoy": 0.02,
                "net_income_improvement": 1000,
            },
            {
                "company_id": "c1",
                "campaign_date": "2021-06-15",
                "revenue_growth_yoy": 0.50,
                "asset_growth_yoy": 0.30,
                "cash_growth_yoy": 0.20,
                "net_income_improvement": 5000,
            },
        ]

        _enrich_growth_features(MagicMock(), rows)

        assert rows[0]["revenue_growth_yoy"] == 0.10
        assert rows[1]["revenue_growth_yoy"] == 0.50
        assert rows[0]["net_income_improvement"] == 1000
        assert rows[1]["net_income_improvement"] == 5000

    @patch("run_backtest.execute_query")
    def test_sql_receives_per_row_dates_via_unnest(self, mock_eq: MagicMock):
        """The SQL params must contain parallel arrays of company_ids and
        as_of_dates so unnest produces per-row pairs."""
        rows = [
            {"company_id": "c1", "campaign_date": "2020-03-01"},
            {"company_id": "c2", "campaign_date": "2021-09-15"},
        ]
        mock_eq.return_value = []

        _enrich_growth_features(MagicMock(), rows)

        call_args = mock_eq.call_args[0]
        _conn, sql, params = call_args
        company_ids, as_of_dates = params

        assert company_ids == ["c1", "c2"]
        assert as_of_dates == ["2020-03-01", "2021-09-15"]
        assert "unnest" in sql.lower()

    @patch("run_backtest.execute_query")
    def test_no_op_when_no_company_ids(self, mock_eq: MagicMock):
        """Rows without company_id should not trigger a query."""
        rows = [{"campaign_date": "2020-01-01"}]
        _enrich_growth_features(MagicMock(), rows)
        mock_eq.assert_not_called()

    @patch("run_backtest.execute_query")
    def test_unmatched_rows_left_unchanged(self, mock_eq: MagicMock):
        """Rows whose (company_id, campaign_date) key has no growth data
        should keep their original values."""
        rows = [
            {"company_id": "c1", "campaign_date": "2020-01-15",
             "revenue_growth_yoy": None},
        ]
        mock_eq.return_value = []  # no growth data found

        _enrich_growth_features(MagicMock(), rows)

        assert rows[0]["revenue_growth_yoy"] is None
