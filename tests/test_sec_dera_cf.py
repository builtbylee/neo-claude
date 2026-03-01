"""Tests for the SEC DERA Crowdfunding Offerings pipeline."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from startuplens.pipelines.sec_dera_cf import (
    _is_quarter_ingested_cf,
    _RateLimiter,
    _safe_float,
    _safe_int,
    download_dera_cf_dataset,
    ingest_dera_cf_batch,
    normalize_dera_cf_record,
    parse_dera_cf_dataset,
    run_dera_cf_pipeline,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_conn() -> MagicMock:
    """Create a mock database connection."""
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value.__enter__ = Mock(return_value=cursor)
    conn.cursor.return_value.__exit__ = Mock(return_value=False)
    cursor.fetchall.return_value = [
        {"id": "test-uuid-123", "source_id": "1234_q2024Q4"},
    ]
    return conn


@pytest.fixture()
def sample_zip(tmp_path: Path) -> Path:
    """Create a minimal DERA CF ZIP with 3 TSV files."""
    import csv
    import io
    import zipfile

    zip_path = tmp_path / "dera_cf_2024_Q4.zip"

    submission_rows = [
        {
            "ACCESSION_NUMBER": "0001-24-000001",
            "SUBMISSION_TYPE": "C",
            "FILING_DATE": "2024-10-15",
            "CIK": "0001234567",
            "FILE_NUMBER": "020-12345",
            "PERIOD": "2024",
        },
        {
            "ACCESSION_NUMBER": "0001-24-000002",
            "SUBMISSION_TYPE": "C-U",
            "FILING_DATE": "2024-11-20",
            "CIK": "0009876543",
            "FILE_NUMBER": "020-67890",
            "PERIOD": "2024",
        },
    ]

    issuer_rows = [
        {
            "ACCESSION_NUMBER": "0001-24-000001",
            "NAMEOFISSUER": "ACME Crowdfund Inc",
            "STATEORCOUNTRY": "CA",
            "DATEINCORPORATION": "2020-03-15",
            "COMPANYNAME": "Wefunder Inc",
            "ISAMENDMENT": "N",
            "PROGRESSUPDATE": "",
            "NATUREOFAMENDMENT": "",
        },
        {
            "ACCESSION_NUMBER": "0001-24-000002",
            "NAMEOFISSUER": "Beta Startup LLC",
            "STATEORCOUNTRY": "NY",
            "DATEINCORPORATION": "2019-01-10",
            "COMPANYNAME": "StartEngine Capital",
            "ISAMENDMENT": "N",
            "PROGRESSUPDATE": "",
            "NATUREOFAMENDMENT": "",
        },
    ]

    disclosure_rows = [
        {
            "ACCESSION_NUMBER": "0001-24-000001",
            "OFFERINGAMOUNT": "500000",
            "MAXIMUMOFFERINGAMOUNT": "1070000",
            "SECURITYOFFEREDTYPE": "Equity",
            "PRICE": "5.00",
            "CURRENTEMPLOYEES": "12",
            "REVENUEMOSTRECENTFISCALYEAR": "150000",
            "REVENUEPRIORFISCALYEAR": "80000",
            "TOTALASSETMOSTRECENTFISCALYEAR": "250000",
            "CASHEQUIMOSTRECENTFISCALYEAR": "50000",
            "NETINCOMEMOSTRECENTFISCALYEAR": "-30000",
            "SHORTTERMDEBTMRECENTFISCALYEAR": "10000",
            "LONGTERMDEBTRECENTFISCALYEAR": "25000",
            "OVERSUBSCRIPTIONACCEPTED": "Y",
            "DEADLINEDATE": "2025-04-15",
        },
        {
            "ACCESSION_NUMBER": "0001-24-000002",
            "OFFERINGAMOUNT": "250000",
            "MAXIMUMOFFERINGAMOUNT": "250000",
            "SECURITYOFFEREDTYPE": "SAFE",
            "PRICE": "",
            "CURRENTEMPLOYEES": "3",
            "REVENUEMOSTRECENTFISCALYEAR": "",
            "REVENUEPRIORFISCALYEAR": "",
            "TOTALASSETMOSTRECENTFISCALYEAR": "15000",
            "CASHEQUIMOSTRECENTFISCALYEAR": "8000",
            "NETINCOMEMOSTRECENTFISCALYEAR": "-50000",
            "SHORTTERMDEBTMRECENTFISCALYEAR": "",
            "LONGTERMDEBTRECENTFISCALYEAR": "",
            "OVERSUBSCRIPTIONACCEPTED": "N",
            "DEADLINEDATE": "",
        },
    ]

    def write_tsv(rows: list[dict]) -> str:
        out = io.StringIO()
        if rows:
            writer = csv.DictWriter(out, fieldnames=rows[0].keys(), delimiter="\t")
            writer.writeheader()
            writer.writerows(rows)
        return out.getvalue()

    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("2024Q4_cf/FORM_C_SUBMISSION.tsv", write_tsv(submission_rows))
        zf.writestr(
            "2024Q4_cf/FORM_C_ISSUER_INFORMATION.tsv", write_tsv(issuer_rows),
        )
        zf.writestr("2024Q4_cf/FORM_C_DISCLOSURE.tsv", write_tsv(disclosure_rows))

    return zip_path


# ---------------------------------------------------------------------------
# Safe parsing helpers
# ---------------------------------------------------------------------------


class TestSafeFloat:
    def test_normal_number(self):
        assert _safe_float("150000") == 150000.0

    def test_with_dollar_and_commas(self):
        assert _safe_float("$1,500,000") == 1_500_000.0

    def test_negative(self):
        assert _safe_float("-30000") == -30000.0

    def test_empty_string(self):
        assert _safe_float("") is None

    def test_none(self):
        assert _safe_float(None) is None

    def test_invalid(self):
        assert _safe_float("N/A") is None


class TestSafeInt:
    def test_normal_int(self):
        assert _safe_int("12") == 12

    def test_with_commas(self):
        assert _safe_int("1,500") == 1500

    def test_float_string(self):
        assert _safe_int("12.5") == 12

    def test_empty(self):
        assert _safe_int("") is None

    def test_none(self):
        assert _safe_int(None) is None


# ---------------------------------------------------------------------------
# parse_dera_cf_dataset
# ---------------------------------------------------------------------------


class TestParseDeraCfDataset:
    def test_joins_three_tsvs(self, sample_zip: Path):
        records = parse_dera_cf_dataset(sample_zip)
        assert len(records) == 2

    def test_includes_submission_fields(self, sample_zip: Path):
        records = parse_dera_cf_dataset(sample_zip)
        assert records[0]["SUBMISSION_TYPE"] == "C"
        assert records[0]["CIK"] == "0001234567"
        assert records[0]["FILING_DATE"] == "2024-10-15"

    def test_includes_issuer_fields(self, sample_zip: Path):
        records = parse_dera_cf_dataset(sample_zip)
        assert records[0]["NAMEOFISSUER"] == "ACME Crowdfund Inc"
        assert records[0]["STATEORCOUNTRY"] == "CA"

    def test_includes_disclosure_fields(self, sample_zip: Path):
        records = parse_dera_cf_dataset(sample_zip)
        assert records[0]["OFFERINGAMOUNT"] == "500000"
        assert records[0]["REVENUEMOSTRECENTFISCALYEAR"] == "150000"
        assert records[0]["CURRENTEMPLOYEES"] == "12"


# ---------------------------------------------------------------------------
# normalize_dera_cf_record
# ---------------------------------------------------------------------------


class TestNormalizeDeraCfRecord:
    def test_extracts_name(self):
        raw = {"NAMEOFISSUER": "ACME Inc", "CIK": "1234567"}
        result = normalize_dera_cf_record(raw)
        assert result["name"] == "ACME Inc"

    def test_strips_leading_zeros_from_cik(self):
        raw = {"CIK": "0001234567"}
        result = normalize_dera_cf_record(raw)
        assert result["cik"] == "1234567"

    def test_defaults_country_to_us(self):
        result = normalize_dera_cf_record({})
        assert result["country"] == "US"

    def test_defaults_source(self):
        result = normalize_dera_cf_record({})
        assert result["source"] == "sec_dera_cf"

    def test_parses_offering_amount(self):
        raw = {"OFFERINGAMOUNT": "500000"}
        result = normalize_dera_cf_record(raw)
        assert result["offering_amount"] == 500000.0

    def test_parses_revenue(self):
        raw = {"REVENUEMOSTRECENTFISCALYEAR": "150000"}
        result = normalize_dera_cf_record(raw)
        assert result["revenue_recent"] == 150000.0

    def test_parses_employees(self):
        raw = {"CURRENTEMPLOYEES": "12"}
        result = normalize_dera_cf_record(raw)
        assert result["employees"] == 12

    def test_classifies_equity_instrument(self):
        raw = {"SECURITYOFFEREDTYPE": "Equity"}
        result = normalize_dera_cf_record(raw)
        assert result["instrument_type"] == "equity"

    def test_classifies_safe_instrument(self):
        raw = {"SECURITYOFFEREDTYPE": "SAFE"}
        result = normalize_dera_cf_record(raw)
        assert result["instrument_type"] == "safe"

    def test_classifies_debt_instrument(self):
        raw = {"SECURITYOFFEREDTYPE": "Debt Securities"}
        result = normalize_dera_cf_record(raw)
        assert result["instrument_type"] == "convertible_note"

    def test_computes_total_debt(self):
        raw = {
            "SHORTTERMDEBTMRECENTFISCALYEAR": "10000",
            "LONGTERMDEBTRECENTFISCALYEAR": "25000",
        }
        result = normalize_dera_cf_record(raw)
        assert result["short_term_debt_recent"] == 10000.0
        assert result["long_term_debt_recent"] == 25000.0

    def test_handles_empty_financials(self):
        raw = {
            "REVENUEMOSTRECENTFISCALYEAR": "",
            "TOTALASSETMOSTRECENTFISCALYEAR": "",
        }
        result = normalize_dera_cf_record(raw)
        assert result["revenue_recent"] is None
        assert result["total_assets_recent"] is None

    def test_oversubscription_accepted(self):
        raw = {"OVERSUBSCRIPTIONACCEPTED": "Y"}
        result = normalize_dera_cf_record(raw)
        assert result["oversubscription_accepted"] is True

    def test_oversubscription_not_accepted(self):
        raw = {"OVERSUBSCRIPTIONACCEPTED": "N"}
        result = normalize_dera_cf_record(raw)
        assert result["oversubscription_accepted"] is False

    def test_missing_name_defaults_to_unknown(self):
        result = normalize_dera_cf_record({})
        assert result["name"] == "Unknown"

    def test_platform_name_from_companyname(self):
        raw = {"COMPANYNAME": "Wefunder Inc"}
        result = normalize_dera_cf_record(raw)
        assert result["platform_name"] == "Wefunder Inc"

    def test_parses_filing_date(self):
        raw = {"FILING_DATE": "2024-10-15"}
        result = normalize_dera_cf_record(raw)
        assert result["filing_date"] == "2024-10-15"


# ---------------------------------------------------------------------------
# RateLimiter
# ---------------------------------------------------------------------------


class TestRateLimiter:
    def test_first_call_does_not_wait(self):
        limiter = _RateLimiter(min_interval=1.0)
        start = time.monotonic()
        limiter.wait()
        elapsed = time.monotonic() - start
        assert elapsed < 0.1

    def test_rapid_calls_are_throttled(self):
        limiter = _RateLimiter(min_interval=0.15)
        limiter.wait()
        start = time.monotonic()
        limiter.wait()
        elapsed = time.monotonic() - start
        assert elapsed >= 0.1


# ---------------------------------------------------------------------------
# download_dera_cf_dataset
# ---------------------------------------------------------------------------


class TestDownloadDeraCfDataset:
    def test_invalid_year_raises(self, tmp_path: Path):
        with pytest.raises(ValueError, match="Year must be"):
            download_dera_cf_dataset(2010, 1, tmp_path)

    def test_invalid_quarter_raises(self, tmp_path: Path):
        with pytest.raises(ValueError, match="Quarter must be"):
            download_dera_cf_dataset(2024, 5, tmp_path)

    def test_skips_if_file_exists(self, tmp_path: Path):
        existing = tmp_path / "dera_cf_2024_Q1.zip"
        existing.write_text("already here")
        result = download_dera_cf_dataset(2024, 1, tmp_path)
        assert result == existing


# ---------------------------------------------------------------------------
# ingest_dera_cf_batch
# ---------------------------------------------------------------------------


class TestIngestDeraCfBatch:
    def test_returns_zero_for_empty_list(self, mock_conn: MagicMock):
        assert ingest_dera_cf_batch(mock_conn, []) == 0
        mock_conn.commit.assert_not_called()

    def test_inserts_records(self, mock_conn: MagicMock):
        records = [
            {
                "name": "Test Corp",
                "country": "US",
                "source": "sec_dera_cf",
                "source_id": "123_q2024Q4",
                "date_incorporation": "2020-01-01",
                "filing_date": "2024-10-15",
                "offering_amount": 500000.0,
                "max_offering_amount": 1070000.0,
                "instrument_type": "equity",
                "platform_name": "Wefunder",
                "revenue_recent": 150000.0,
                "total_assets_recent": 250000.0,
                "cash_recent": 50000.0,
                "net_income_recent": -30000.0,
                "short_term_debt_recent": 10000.0,
                "long_term_debt_recent": 25000.0,
                "employees": 12,
            },
        ]
        count = ingest_dera_cf_batch(mock_conn, records)
        assert count == 1
        mock_conn.commit.assert_called_once()


# ---------------------------------------------------------------------------
# _is_quarter_ingested_cf
# ---------------------------------------------------------------------------


class TestIsQuarterIngested:
    @patch("startuplens.db.execute_query", return_value=[{"cnt": 100}])
    def test_returns_true_when_ingested(self, mock_eq: MagicMock):
        conn = MagicMock()
        assert _is_quarter_ingested_cf(conn, 2024, 4) is True

    @patch("startuplens.db.execute_query", return_value=[{"cnt": 0}])
    def test_returns_false_when_not_ingested(self, mock_eq: MagicMock):
        conn = MagicMock()
        assert _is_quarter_ingested_cf(conn, 2024, 4) is False

    @patch("startuplens.db.execute_query", return_value=[{"cnt": 3}])
    def test_returns_false_when_partial_ingest(self, mock_eq: MagicMock):
        conn = MagicMock()
        assert _is_quarter_ingested_cf(conn, 2024, 4) is False

    @patch("startuplens.db.execute_query", return_value=[{"cnt": 50}])
    def test_uses_regex_operator(self, mock_eq: MagicMock):
        """Query must use ~ (regex) not LIKE, for robust pattern matching."""
        conn = MagicMock()
        _is_quarter_ingested_cf(conn, 2024, 4)
        _conn, sql, params = mock_eq.call_args[0]
        assert "~ %s" in sql
        assert params == ("_q2024Q4($|_)",)


class TestIsQuarterIngestedPattern:
    """Verify the regex matches both legacy and accession-suffixed source_ids."""

    def test_legacy_format_matches(self):
        """Legacy: {cik}_q{year}Q{quarter}"""
        import re
        pattern = "_q2024Q4($|_)"
        assert re.search(pattern, "1234567_q2024Q4")

    def test_accession_suffixed_format_matches(self):
        """New: {cik}_q{year}Q{quarter}_{accession}"""
        import re
        pattern = "_q2024Q4($|_)"
        assert re.search(pattern, "1234567_q2024Q4_0001-24-000001")

    def test_different_quarter_does_not_match(self):
        import re
        pattern = "_q2024Q4($|_)"
        assert not re.search(pattern, "1234567_q2024Q3")
        assert not re.search(pattern, "1234567_q2024Q3_0001-24-000001")

    def test_different_year_does_not_match(self):
        import re
        pattern = "_q2024Q4($|_)"
        assert not re.search(pattern, "1234567_q2023Q4")

    def test_no_false_positive_on_partial_quarter(self):
        """_q2024Q4 should not match _q2024Q41 (hypothetical)."""
        import re
        pattern = "_q2024Q4($|_)"
        assert not re.search(pattern, "1234567_q2024Q41")


# ---------------------------------------------------------------------------
# run_dera_cf_pipeline (mocked)
# ---------------------------------------------------------------------------


class TestRunDeraCfPipeline:
    @patch("startuplens.db.get_connection")
    @patch(
        "startuplens.pipelines.sec_dera_cf._is_quarter_ingested_cf",
        return_value=True,
    )
    def test_skips_ingested_quarters(
        self, mock_ingested: MagicMock, mock_get_conn: MagicMock, tmp_path: Path,
    ):
        conn = MagicMock()
        mock_get_conn.return_value = MagicMock()
        settings = MagicMock()

        summary = run_dera_cf_pipeline(
            conn, settings, [2024], output_dir=tmp_path,
        )

        assert summary["quarters_skipped"] == 4
        assert summary["quarters_processed"] == 0
        assert summary["records_ingested"] == 0
