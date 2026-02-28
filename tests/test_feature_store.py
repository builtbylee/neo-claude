"""Tests for the feature store core and all 7 feature extractors.

All DB calls are mocked — no real database needed.
"""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock

import pytest

from startuplens.feature_store.extractors.campaign import extract_campaign_features
from startuplens.feature_store.extractors.company import extract_company_features
from startuplens.feature_store.extractors.financial import extract_financial_features
from startuplens.feature_store.extractors.market_regime import extract_market_regime_features
from startuplens.feature_store.extractors.regulatory import extract_regulatory_features
from startuplens.feature_store.extractors.team import extract_team_features
from startuplens.feature_store.extractors.terms import extract_terms_features
from startuplens.feature_store.store import (
    read_features_as_of,
    read_training_matrix,
    validate_feature_write,
    write_feature,
    write_features_batch,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_conn() -> MagicMock:
    """Create a mock psycopg connection with cursor context manager."""
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    return conn


# ===========================================================================
# Store: validate_feature_write
# ===========================================================================


class TestValidateFeatureWrite:
    def test_valid_numeric_feature(self):
        assert validate_feature_write("funding_target", 100000) is True

    def test_valid_boolean_feature(self):
        assert validate_feature_write("eis_seis_eligible", True) is True

    def test_valid_categorical_feature(self):
        assert validate_feature_write("platform", "seedrs") is True

    def test_none_value_always_valid(self):
        assert validate_feature_write("funding_target", None) is True

    def test_unknown_feature_invalid(self):
        assert validate_feature_write("nonexistent_feature", 42) is False

    def test_wrong_dtype_numeric_gets_string(self):
        assert validate_feature_write("funding_target", "not a number") is False

    def test_wrong_dtype_boolean_gets_int(self):
        assert validate_feature_write("eis_seis_eligible", 1) is False

    def test_wrong_dtype_categorical_gets_number(self):
        assert validate_feature_write("platform", 42) is False

    def test_float_is_valid_numeric(self):
        assert validate_feature_write("overfunding_ratio", 1.5) is True


# ===========================================================================
# Store: write_feature
# ===========================================================================


class TestWriteFeature:
    def test_writes_valid_feature(self):
        conn = _mock_conn()
        write_feature(
            conn,
            entity_id="abc-123",
            feature_name="funding_target",
            value=50000,
            as_of_date=date(2024, 1, 15),
            source="seedrs_scrape",
        )
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.execute.assert_called_once()
        args = cursor.execute.call_args
        params = args[0][1]
        assert params[0] == "abc-123"
        assert params[1] == date(2024, 1, 15)
        assert params[2] == "campaign"  # family from registry
        assert params[3] == "funding_target"
        assert json.loads(params[4]) == {"value": 50000}
        assert params[5] == "seedrs_scrape"
        assert params[6] == 3  # default tier

    def test_rejects_unknown_feature(self):
        conn = _mock_conn()
        with pytest.raises(ValueError, match="Unknown feature"):
            write_feature(
                conn,
                entity_id="abc-123",
                feature_name="totally_fake",
                value=42,
                as_of_date=date(2024, 1, 1),
                source="test",
            )

    def test_custom_label_tier(self):
        conn = _mock_conn()
        write_feature(
            conn,
            entity_id="abc-123",
            feature_name="sector",
            value="fintech",
            as_of_date=date(2024, 6, 1),
            source="companies_house",
            label_tier=1,
        )
        cursor = conn.cursor.return_value.__enter__.return_value
        params = cursor.execute.call_args[0][1]
        assert params[6] == 1

    def test_boolean_feature_stored_as_jsonb(self):
        conn = _mock_conn()
        write_feature(
            conn,
            entity_id="abc-123",
            feature_name="eis_seis_eligible",
            value=True,
            as_of_date=date(2024, 1, 1),
            source="test",
        )
        cursor = conn.cursor.return_value.__enter__.return_value
        params = cursor.execute.call_args[0][1]
        assert json.loads(params[4]) == {"value": True}


# ===========================================================================
# Store: write_features_batch
# ===========================================================================


class TestWriteFeaturesBatch:
    def test_batch_writes_multiple_features(self):
        conn = _mock_conn()
        features = {
            "funding_target": 100000,
            "amount_raised": 120000,
            "platform": "seedrs",
        }
        count = write_features_batch(
            conn,
            entity_id="entity-001",
            features=features,
            as_of_date=date(2024, 3, 1),
            source="batch_import",
        )
        assert count == 3
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.executemany.assert_called_once()

    def test_batch_skips_none_values(self):
        conn = _mock_conn()
        features = {
            "funding_target": 100000,
            "amount_raised": None,
            "platform": "seedrs",
        }
        count = write_features_batch(
            conn,
            entity_id="entity-001",
            features=features,
            as_of_date=date(2024, 3, 1),
            source="batch_import",
        )
        assert count == 2

    def test_batch_rejects_unknown_features(self):
        conn = _mock_conn()
        with pytest.raises(ValueError, match="Unknown features"):
            write_features_batch(
                conn,
                entity_id="entity-001",
                features={"fake_feature": 42},
                as_of_date=date(2024, 1, 1),
                source="test",
            )

    def test_batch_empty_after_nones_returns_zero(self):
        conn = _mock_conn()
        count = write_features_batch(
            conn,
            entity_id="entity-001",
            features={"funding_target": None, "amount_raised": None},
            as_of_date=date(2024, 1, 1),
            source="test",
        )
        assert count == 0


# ===========================================================================
# Store: read_features_as_of (temporal correctness)
# ===========================================================================


class TestReadFeaturesAsOf:
    def test_returns_most_recent_before_date(self):
        """Temporal correctness: returns only data on or before as_of_date."""
        conn = _mock_conn()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.description = True
        cursor.fetchall.return_value = [
            {"feature_name": "funding_target", "feature_value": {"value": 50000}},
            {"feature_name": "sector", "feature_value": {"value": "fintech"}},
        ]

        result = read_features_as_of(conn, "entity-001", date(2024, 6, 1))

        assert result == {"funding_target": 50000, "sector": "fintech"}
        # Verify the SQL uses <= for temporal correctness
        sql = cursor.execute.call_args[0][0]
        assert "<=" in sql
        params = cursor.execute.call_args[0][1]
        assert params == ("entity-001", date(2024, 6, 1))

    def test_returns_empty_when_no_features(self):
        conn = _mock_conn()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.description = True
        cursor.fetchall.return_value = []

        result = read_features_as_of(conn, "entity-001", date(2020, 1, 1))
        assert result == {}

    def test_handles_string_jsonb(self):
        """Handles case where psycopg returns JSONB as string."""
        conn = _mock_conn()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.description = True
        cursor.fetchall.return_value = [
            {"feature_name": "platform", "feature_value": '{"value": "crowdcube"}'},
        ]

        result = read_features_as_of(conn, "entity-001", date(2024, 6, 1))
        assert result == {"platform": "crowdcube"}


# ===========================================================================
# Store: read_training_matrix
# ===========================================================================


class TestReadTrainingMatrix:
    def test_returns_wide_format(self):
        conn = _mock_conn()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.description = True
        cursor.fetchall.return_value = [
            {"entity_id": "e1", "feature_name": "funding_target", "feature_value": {"value": 100}},
            {"entity_id": "e1", "feature_name": "sector", "feature_value": {"value": "saas"}},
            {"entity_id": "e2", "feature_name": "funding_target", "feature_value": {"value": 200}},
        ]

        result = read_training_matrix(conn, date(2024, 6, 1))

        assert len(result) == 2
        e1 = [r for r in result if r["entity_id"] == "e1"][0]
        assert e1["funding_target"] == 100
        assert e1["sector"] == "saas"
        e2 = [r for r in result if r["entity_id"] == "e2"][0]
        assert e2["funding_target"] == 200

    def test_passes_label_tier_filter(self):
        conn = _mock_conn()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.description = True
        cursor.fetchall.return_value = []

        read_training_matrix(conn, date(2024, 6, 1), min_label_tier=1)

        params = cursor.execute.call_args[0][1]
        assert params[1] == 1  # min_label_tier passed to SQL


# ===========================================================================
# Extractor: Campaign
# ===========================================================================


class TestCampaignExtractor:
    def test_basic_extraction(self):
        record = {
            "funding_target": 100000,
            "amount_raised": 150000,
            "equity_offered_pct": 10.0,
            "pre_money_valuation": 900000,
            "investor_count": 250,
            "funding_velocity_days": 30,
            "eis_seis_eligible": True,
            "platform": "seedrs",
        }
        features = extract_campaign_features(record)
        assert features["funding_target"] == 100000
        assert features["amount_raised"] == 150000
        assert features["overfunding_ratio"] == 1.5
        assert features["platform"] == "seedrs"

    def test_overfunding_ratio_computed(self):
        record = {"funding_target": 200000, "amount_raised": 400000}
        features = extract_campaign_features(record)
        assert features["overfunding_ratio"] == 2.0

    def test_overfunding_ratio_zero_target(self):
        record = {"funding_target": 0, "amount_raised": 100000}
        features = extract_campaign_features(record)
        assert features["overfunding_ratio"] == 0.0

    def test_overfunding_ratio_missing_fields(self):
        features = extract_campaign_features({})
        assert features["overfunding_ratio"] is None

    def test_missing_fields_return_none(self):
        features = extract_campaign_features({})
        assert features["funding_target"] is None
        assert features["platform"] is None


# ===========================================================================
# Extractor: Company
# ===========================================================================


class TestCompanyExtractor:
    def test_basic_extraction(self):
        record = {
            "incorporation_date": "2020-01-15",
            "campaign_date": "2024-01-15",
            "employee_count": 12,
            "revenue_at_raise": 500000,
            "revenue_growth_rate": 0.35,
            "total_prior_funding": 250000,
            "prior_vc_backing": True,
            "sector": "fintech",
            "revenue_model_type": "recurring",
            "country": "GB",
        }
        features = extract_company_features(record)
        assert features["company_age_months"] == 48
        assert features["employee_count"] == 12
        assert features["pre_revenue"] is False
        assert features["sector"] == "fintech"

    def test_company_age_months_computed(self):
        record = {
            "incorporation_date": "2022-06-01",
            "campaign_date": "2024-06-01",
        }
        features = extract_company_features(record)
        assert features["company_age_months"] == 24

    def test_company_age_missing_dates(self):
        features = extract_company_features({})
        assert features["company_age_months"] is None

    def test_pre_revenue_when_no_revenue(self):
        features = extract_company_features({})
        assert features["pre_revenue"] is True

    def test_pre_revenue_when_zero_revenue(self):
        features = extract_company_features({"revenue_at_raise": 0})
        assert features["pre_revenue"] is True

    def test_pre_revenue_when_has_revenue(self):
        features = extract_company_features({"revenue_at_raise": 100000})
        assert features["pre_revenue"] is False

    def test_company_age_with_date_objects(self):
        record = {
            "incorporation_date": date(2023, 1, 1),
            "campaign_date": date(2024, 7, 1),
        }
        features = extract_company_features(record)
        # 547 days / 30 = 18
        assert features["company_age_months"] == 18

    def test_company_age_negative_returns_zero(self):
        record = {
            "incorporation_date": "2025-01-01",
            "campaign_date": "2024-01-01",
        }
        features = extract_company_features(record)
        assert features["company_age_months"] == 0


# ===========================================================================
# Extractor: Financial
# ===========================================================================


class TestFinancialExtractor:
    def test_basic_extraction(self):
        record = {
            "total_assets": 500000,
            "total_debt": 100000,
            "cash_position": 200000,
            "burn_rate_monthly": 25000,
            "gross_margin": 0.65,
        }
        features = extract_financial_features(record)
        assert features["total_assets"] == 500000
        assert features["debt_to_asset_ratio"] == pytest.approx(0.2)
        assert features["gross_margin"] == 0.65

    def test_debt_to_asset_ratio_computed(self):
        record = {"total_assets": 1000000, "total_debt": 250000}
        features = extract_financial_features(record)
        assert features["debt_to_asset_ratio"] == 0.25

    def test_debt_to_asset_ratio_zero_assets(self):
        record = {"total_assets": 0, "total_debt": 100000}
        features = extract_financial_features(record)
        assert features["debt_to_asset_ratio"] == 0.0

    def test_debt_to_asset_ratio_missing_fields(self):
        features = extract_financial_features({})
        assert features["debt_to_asset_ratio"] is None


# ===========================================================================
# Extractor: Team
# ===========================================================================


class TestTeamExtractor:
    def test_basic_extraction(self):
        record = {
            "founder_count": 2,
            "domain_experience_years": 15,
            "prior_exits": True,
            "accelerator_alumni": False,
        }
        features = extract_team_features(record)
        assert features["founder_count"] == 2
        assert features["domain_experience_years"] == 15
        assert features["prior_exits"] is True
        assert features["accelerator_alumni"] is False

    def test_missing_fields_return_none(self):
        features = extract_team_features({})
        assert features["founder_count"] is None
        assert features["prior_exits"] is None


# ===========================================================================
# Extractor: Terms
# ===========================================================================


class TestTermsExtractor:
    def test_basic_extraction(self):
        record = {
            "instrument_type": "safe",
            "valuation_cap": 5000000,
            "discount_rate": 0.20,
            "mfn_clause": True,
            "liquidation_pref_multiple": 1.0,
            "liquidation_participation": "non_participating",
            "seniority_position": 1,
            "pro_rata_rights": True,
            "qualified_institutional": False,
        }
        features = extract_terms_features(record)
        assert features["instrument_type"] == "safe"
        assert features["valuation_cap"] == 5000000
        assert features["discount_rate"] == 0.20
        assert features["mfn_clause"] is True

    def test_missing_fields_return_none(self):
        features = extract_terms_features({})
        for key in features:
            assert features[key] is None


# ===========================================================================
# Extractor: Regulatory
# ===========================================================================


class TestRegulatoryExtractor:
    def test_basic_extraction(self):
        record = {
            "company_status": "active",
            "accounts_overdue": False,
            "charges_count": 2,
            "director_disqualifications": 0,
        }
        features = extract_regulatory_features(record)
        assert features["company_status"] == "active"
        assert features["accounts_overdue"] is False
        assert features["charges_count"] == 2

    def test_missing_fields_return_none(self):
        features = extract_regulatory_features({})
        assert features["company_status"] is None
        assert features["charges_count"] is None


# ===========================================================================
# Extractor: Market Regime
# ===========================================================================


class TestMarketRegimeExtractor:
    def test_basic_extraction(self):
        record = {
            "interest_rate_regime": "rising",
            "equity_market_regime": "bull",
            "ecf_quarterly_volume": 150,
        }
        features = extract_market_regime_features(record)
        assert features["interest_rate_regime"] == "rising"
        assert features["equity_market_regime"] == "bull"
        assert features["ecf_quarterly_volume"] == 150

    def test_missing_fields_return_none(self):
        features = extract_market_regime_features({})
        assert features["interest_rate_regime"] is None
        assert features["ecf_quarterly_volume"] is None


# ===========================================================================
# Integration-style tests (still mocked DB)
# ===========================================================================


class TestFeatureStoreIntegration:
    def test_extractor_outputs_are_valid_feature_names(self):
        """All keys returned by extractors must be in the registry."""
        sample_record = {
            "funding_target": 100000,
            "amount_raised": 150000,
            "incorporation_date": "2020-01-01",
            "campaign_date": "2024-01-01",
            "total_assets": 500000,
            "total_debt": 100000,
        }
        all_features: dict = {}
        all_features.update(extract_campaign_features(sample_record))
        all_features.update(extract_company_features(sample_record))
        all_features.update(extract_financial_features(sample_record))
        all_features.update(extract_team_features(sample_record))
        all_features.update(extract_terms_features(sample_record))
        all_features.update(extract_regulatory_features(sample_record))
        all_features.update(extract_market_regime_features(sample_record))

        from startuplens.feature_store.registry import is_valid_feature

        for name in all_features:
            assert is_valid_feature(name), f"Extractor returned unregistered feature: {name}"

    def test_validate_rejects_all_extractor_bad_types(self):
        """Sanity: numeric features reject string, boolean reject int, etc."""
        assert validate_feature_write("funding_target", "bad") is False
        assert validate_feature_write("eis_seis_eligible", 0) is False
        assert validate_feature_write("platform", 123) is False
