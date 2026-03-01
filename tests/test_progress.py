"""Tests for progress model training and label construction."""

from __future__ import annotations

import numpy as np

from startuplens.model.train import (
    PROGRESS_CATEGORICAL_FEATURES,
    PROGRESS_FEATURE_COLUMNS,
    TrainedModel,
    train_progress_model,
)


def _make_rows(n: int, *, progressed_ids: set[str]) -> list[dict]:
    """Build synthetic feature rows with company_id field."""
    rng = np.random.RandomState(42)
    rows = []
    for i in range(n):
        cid = f"company-{i:04d}"
        is_prog = cid in progressed_ids
        # Progressed companies tend to have higher revenue growth
        rev_growth = rng.normal(0.8 if is_prog else -0.2, 0.3)
        rows.append({
            "entity_id": f"entity-{i:04d}",
            "company_id": cid,
            "campaign_date": f"2020-{(i % 12) + 1:02d}-15",
            "outcome": "trading" if is_prog else "failed",
            "company_age_months": int(rng.uniform(6, 120)),
            "employee_count": int(rng.uniform(1, 50)),
            "revenue_at_raise": float(rng.uniform(0, 500000)),
            "pre_revenue": rng.random() < 0.3,
            "total_assets": float(rng.uniform(10000, 2000000)),
            "total_debt": float(rng.uniform(0, 500000)),
            "debt_to_asset_ratio": float(rng.uniform(0, 1)),
            "cash_position": float(rng.uniform(0, 500000)),
            "funding_target": float(rng.uniform(50000, 1000000)),
            "amount_raised": float(rng.uniform(50000, 1500000)),
            "overfunding_ratio": float(rng.uniform(0.5, 3.0)),
            "instrument_type": ["equity", "safe", "convertible_note"][i % 3],
            "platform": ["republic", "wefunder", "startengine"][i % 3],
            "country": "US",
            "revenue_growth_yoy": float(rev_growth),
            "asset_growth_yoy": float(rng.normal(0.5 if is_prog else -0.1, 0.4)),
            "cash_growth_yoy": float(rng.normal(0.3 if is_prog else -0.3, 0.5)),
            "net_income_improvement": float(rng.normal(50000 if is_prog else -20000, 30000)),
        })
    return rows


class TestTrainProgressModel:
    """Tests for train_progress_model()."""

    def test_returns_trained_model(self):
        """With sufficient separable data, returns a TrainedModel."""
        n_train, n_test = 200, 60
        progressed = {f"company-{i:04d}" for i in range(0, n_train + n_test, 3)}
        all_rows = _make_rows(n_train + n_test, progressed_ids=progressed)
        train_rows = all_rows[:n_train]
        test_rows = all_rows[n_train:]

        train_labels = {r["company_id"]: (1 if r["company_id"] in progressed else 0)
                        for r in train_rows}
        test_labels = {r["company_id"]: (1 if r["company_id"] in progressed else 0)
                       for r in test_rows}

        result = train_progress_model(train_rows, test_rows, train_labels, test_labels)
        assert result is not None
        assert isinstance(result, TrainedModel)
        assert result.n_train > 0
        assert result.n_test > 0

    def test_auc_above_random(self):
        """With clearly separable data, AUC should exceed 0.5."""
        n_train, n_test = 300, 80
        progressed = {f"company-{i:04d}" for i in range(0, n_train + n_test, 3)}
        all_rows = _make_rows(n_train + n_test, progressed_ids=progressed)
        train_rows = all_rows[:n_train]
        test_rows = all_rows[n_train:]

        train_labels = {r["company_id"]: (1 if r["company_id"] in progressed else 0)
                        for r in train_rows}
        test_labels = {r["company_id"]: (1 if r["company_id"] in progressed else 0)
                       for r in test_rows}

        result = train_progress_model(train_rows, test_rows, train_labels, test_labels)
        assert result is not None
        assert result.auc > 0.5

    def test_growth_features_in_importances(self):
        """Growth features should appear in feature importances."""
        n_train, n_test = 200, 60
        progressed = {f"company-{i:04d}" for i in range(0, n_train + n_test, 3)}
        all_rows = _make_rows(n_train + n_test, progressed_ids=progressed)
        train_rows = all_rows[:n_train]
        test_rows = all_rows[n_train:]

        train_labels = {r["company_id"]: (1 if r["company_id"] in progressed else 0)
                        for r in train_rows}
        test_labels = {r["company_id"]: (1 if r["company_id"] in progressed else 0)
                       for r in test_rows}

        result = train_progress_model(train_rows, test_rows, train_labels, test_labels)
        assert result is not None
        assert "revenue_growth_yoy" in result.feature_importances
        assert "asset_growth_yoy" in result.feature_importances

    def test_returns_none_insufficient_data(self):
        """Returns None when not enough labeled data."""
        rows = _make_rows(10, progressed_ids=set())
        labels = {r["company_id"]: 0 for r in rows}
        result = train_progress_model(rows[:5], rows[5:], labels, labels)
        assert result is None

    def test_returns_none_single_class(self):
        """Returns None when only one class in labels."""
        n = 100
        rows = _make_rows(n, progressed_ids=set())
        # All negative — no positive class
        labels = {r["company_id"]: 0 for r in rows}
        result = train_progress_model(rows[:60], rows[60:], labels, labels)
        assert result is None

    def test_feature_columns_include_growth(self):
        """PROGRESS_FEATURE_COLUMNS includes growth features."""
        assert "revenue_growth_yoy" in PROGRESS_FEATURE_COLUMNS
        assert "asset_growth_yoy" in PROGRESS_FEATURE_COLUMNS
        assert "cash_growth_yoy" in PROGRESS_FEATURE_COLUMNS
        assert "net_income_improvement" in PROGRESS_FEATURE_COLUMNS

    def test_categorical_features_match_survival(self):
        """Progress and survival models use the same categorical features."""
        from startuplens.model.train import CATEGORICAL_FEATURES
        assert PROGRESS_CATEGORICAL_FEATURES == CATEGORICAL_FEATURES


# ------------------------------------------------------------------
# Progress label cutoff_date regression
# ------------------------------------------------------------------


class TestProgressLabelCutoff:
    """Verify cutoff_date caps both maturity gate and evidence windows."""

    def test_cutoff_date_passed_five_times(self):
        """cutoff_date must appear 3× in params: maturity gate, follow_on
        evidence cap, revenue_progress evidence cap."""
        from unittest.mock import MagicMock, patch

        from startuplens.model.progress_labels import load_progress_labels

        conn = MagicMock()
        with patch("startuplens.model.progress_labels.execute_query",
                    return_value=[]) as mock_eq:
            load_progress_labels(conn, "2020-01-01", "2021-12-31",
                                 cutoff_date="2022-06-30")

        _conn, _sql, params = mock_eq.call_args[0]
        assert params == (
            "2020-01-01", "2021-12-31",
            "2022-06-30", "2022-06-30", "2022-06-30",
        )

    def test_cutoff_defaults_to_end(self):
        """When cutoff_date is omitted, it defaults to end."""
        from unittest.mock import MagicMock, patch

        from startuplens.model.progress_labels import load_progress_labels

        conn = MagicMock()
        with patch("startuplens.model.progress_labels.execute_query",
                    return_value=[]) as mock_eq:
            load_progress_labels(conn, "2020-01-01", "2021-12-31")

        _conn, _sql, params = mock_eq.call_args[0]
        # cutoff_date should be "2021-12-31" (= end) in all 3 positions
        assert params[2] == "2021-12-31"
        assert params[3] == "2021-12-31"
        assert params[4] == "2021-12-31"

    def test_query_contains_least_caps(self):
        """Both evidence CTEs must use LEAST(..., cutoff_date) to cap."""
        from startuplens.model.progress_labels import _PROGRESS_LABEL_QUERY

        # Count LEAST occurrences — one per evidence CTE
        least_count = _PROGRESS_LABEL_QUERY.upper().count("LEAST(")
        assert least_count == 2, (
            f"Expected 2 LEAST() caps (follow_on + revenue_progress), "
            f"got {least_count}"
        )
