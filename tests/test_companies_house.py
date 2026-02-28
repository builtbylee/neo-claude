"""Tests for the Companies House pipeline."""

from datetime import date
from unittest.mock import MagicMock, patch

from startuplens.pipelines.companies_house import (
    fetch_company_profile,
    get_verified_company_numbers,
    ingest_company_batch,
    normalize_company_profile,
)


class TestNormalizeCompanyProfile:
    def test_basic_profile(self):
        raw = {
            "company_number": "12345678",
            "company_name": "Test Ltd",
            "company_status": "active",
            "date_of_creation": "2020-03-15",
            "sic_codes": ["62012"],
            "registered_office_address": {"locality": "London"},
            "accounts": {
                "last_accounts": {"made_up_to": "2024-06-30"},
                "overdue": False,
            },
            "has_charges": True,
        }
        result = normalize_company_profile(raw)
        assert result["company_number"] == "12345678"
        assert result["company_name"] == "Test Ltd"
        assert result["company_status"] == "active"
        assert result["incorporation_date"] == date(2020, 3, 15)
        assert result["sic_codes"] == ["62012"]
        assert result["last_accounts_date"] == date(2024, 6, 30)
        assert result["accounts_overdue"] is False
        assert result["has_charges"] is True
        assert result["country"] == "UK"

    def test_missing_optional_fields(self):
        raw = {
            "company_number": "00000001",
            "company_name": "Bare Minimum Ltd",
            "company_status": "dissolved",
        }
        result = normalize_company_profile(raw)
        assert result["incorporation_date"] is None
        assert result["sic_codes"] == []
        assert result["last_accounts_date"] is None
        assert result["accounts_overdue"] is False
        assert result["has_charges"] is False

    def test_dissolved_status(self):
        raw = {
            "company_number": "99999999",
            "company_name": "Gone Ltd",
            "company_status": "dissolved",
            "date_of_creation": "2015-01-01",
        }
        result = normalize_company_profile(raw)
        assert result["company_status"] == "dissolved"


class TestIngestCompanyBatch:
    def test_inserts_records(self):
        conn = MagicMock()
        companies = [
            {
                "company_number": "12345678",
                "company_name": "Test Ltd",
                "incorporation_date": date(2020, 1, 1),
                "sic_codes": ["62012"],
                "country": "UK",
                "company_status": "active",
            },
        ]
        with patch(
            "startuplens.pipelines.companies_house.execute_many",
            return_value=1,
        ) as mock:
            result = ingest_company_batch(conn, companies)
        assert result == 1
        mock.assert_called_once()

    def test_empty_batch_returns_zero(self):
        conn = MagicMock()
        result = ingest_company_batch(conn, [])
        assert result == 0


class TestGetVerifiedCompanyNumbers:
    def test_returns_set_of_numbers(self):
        conn = MagicMock()
        with patch(
            "startuplens.pipelines.companies_house.execute_query",
            return_value=[
                {"source_id": "12345678"},
                {"source_id": "87654321"},
            ],
        ):
            result = get_verified_company_numbers(conn)
        assert result == {"12345678", "87654321"}

    def test_empty_result(self):
        conn = MagicMock()
        with patch(
            "startuplens.pipelines.companies_house.execute_query",
            return_value=[],
        ):
            result = get_verified_company_numbers(conn)
        assert result == set()


class TestFetchCompanyProfile:
    def test_returns_json_on_success(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"company_number": "12345678"}
        mock_client.get.return_value = mock_response
        result = fetch_company_profile(mock_client, "12345678")
        assert result == {"company_number": "12345678"}

    def test_returns_none_on_404(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_client.get.return_value = mock_response
        result = fetch_company_profile(mock_client, "00000000")
        assert result is None
