"""Tests for the SEC EDGAR Form C pipeline."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from startuplens.pipelines.sec_edgar import (
    _classify_instrument_type,
    _classify_round_type,
    _RateLimiter,
    derive_sec_outcomes,
    download_form_c_index,
    ingest_form_c_batch,
    normalize_form_c_record,
    parse_form_c_filings,
    run_sec_pipeline,
)

# Path to test fixtures
FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def sample_index_file(tmp_path: Path) -> Path:
    """Create a minimal EDGAR-format index file for testing."""
    # SEC EDGAR index is fixed-width; lines must be wide enough for parsing
    lines = [
        "CIK|Company Name|Form Type|Date Filed|Filename",
        "-----------------------------------------------------------",
        "ACME CROWDFUND INC      C       1234567 2023-01-15 "
        "edgar/data/1234567/0001.txt",
        "BETA STARTUP LLC        C-U     9876543 2023-02-20 "
        "edgar/data/9876543/0001.txt",
        "GAMMA TECH CORP         C/A     1111111 2023-03-10 "
        "edgar/data/1111111/0001.txt",
        "DELTA HEALTH INC        C-AR    2222222 2023-03-25 "
        "edgar/data/2222222/0001.txt",
        "EPSILON ENERGY LLC      10-K    3333333 2023-01-30 "
        "edgar/data/3333333/0001.txt",
        "ZETA FINTECH INC        C       4444444 2023-04-01 "
        "edgar/data/4444444/0001.txt",
    ]
    idx_file = tmp_path / "company_2023_Q1.idx"
    idx_file.write_text("\n".join(lines) + "\n")
    return idx_file


@pytest.fixture()
def mock_conn() -> MagicMock:
    """Create a mock database connection."""
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value.__enter__ = Mock(return_value=cursor)
    conn.cursor.return_value.__exit__ = Mock(return_value=False)

    # Make cursor.fetchone() return a dict with an id
    cursor.fetchone.return_value = {"id": "test-uuid-123"}

    return conn


# ---------------------------------------------------------------------------
# parse_form_c_filings tests
# ---------------------------------------------------------------------------

class TestParseFormCFilings:
    def test_filters_to_form_c_types_only(self, sample_index_file: Path):
        """Only Form C variants should be returned, not 10-K etc."""
        filings = parse_form_c_filings(sample_index_file)

        form_types = {f["form_type"] for f in filings}
        assert "10-K" not in form_types
        # Should have at least the C, C-U, C/A, C-AR entries
        assert form_types <= {"C", "C-U", "C/A", "C-AR", "C-AR/A", "C-TR", "C-U/A"}

    def test_extracts_company_name(self, sample_index_file: Path):
        filings = parse_form_c_filings(sample_index_file)
        names = [f["company_name"] for f in filings]
        # At least some company names should be present
        assert len(names) > 0
        assert all(isinstance(n, str) and len(n) > 0 for n in names)

    def test_returns_list_of_dicts(self, sample_index_file: Path):
        filings = parse_form_c_filings(sample_index_file)
        assert isinstance(filings, list)
        for f in filings:
            assert isinstance(f, dict)
            assert "form_type" in f
            assert "cik" in f
            assert "date_filed" in f

    def test_empty_file_returns_empty_list(self, tmp_path: Path):
        empty_file = tmp_path / "empty.idx"
        empty_file.write_text("")
        assert parse_form_c_filings(empty_file) == []

    def test_file_with_only_headers_returns_empty(self, tmp_path: Path):
        header_only = tmp_path / "header.idx"
        header_only.write_text(
            "CIK|Company Name|Form Type|Date Filed|Filename\n"
            "-----------------------------------------------------------\n"
        )
        assert parse_form_c_filings(header_only) == []


# ---------------------------------------------------------------------------
# normalize_form_c_record tests
# ---------------------------------------------------------------------------

class TestNormalizeFormCRecord:
    def test_maps_field_names(self):
        raw = {
            "company_name": "ACME INC",
            "cik": "0001234567",
            "date_filed": "2023-01-15",
        }
        result = normalize_form_c_record(raw)
        assert result["name"] == "ACME INC"
        assert result["source_id"] == "1234567"  # leading zeros stripped
        assert result["filing_date"] == "2023-01-15"

    def test_strips_leading_zeros_from_cik(self):
        raw = {"cik": "0000001234"}
        result = normalize_form_c_record(raw)
        assert result["source_id"] == "1234"

    def test_all_zero_cik_becomes_zero(self):
        raw = {"cik": "0000000000"}
        result = normalize_form_c_record(raw)
        assert result["source_id"] == "0"

    def test_defaults_country_to_us(self):
        result = normalize_form_c_record({})
        assert result["country"] == "US"

    def test_defaults_source_to_sec_edgar(self):
        result = normalize_form_c_record({})
        assert result["source"] == "sec_edgar"

    def test_coerces_numeric_amount_raised(self):
        raw = {"total_amount_sold": "$1,500,000"}
        result = normalize_form_c_record(raw)
        assert result["amount_raised"] == 1_500_000.0

    def test_handles_empty_numeric_string(self):
        raw = {"total_amount_sold": ""}
        result = normalize_form_c_record(raw)
        assert result.get("amount_raised") is None

    def test_handles_non_numeric_string(self):
        raw = {"total_amount_sold": "N/A"}
        result = normalize_form_c_record(raw)
        assert result.get("amount_raised") is None

    def test_normalizes_sector_to_lowercase(self):
        raw = {"issuer_industry": "Technology"}
        result = normalize_form_c_record(raw)
        assert result["sector"] == "technology"

    def test_empty_sector_becomes_none(self):
        raw = {"issuer_industry": "  "}
        result = normalize_form_c_record(raw)
        assert result["sector"] is None

    def test_preserves_form_type(self):
        raw = {"form_type": "C-U"}
        result = normalize_form_c_record(raw)
        assert result["form_type"] == "C-U"

    def test_missing_name_defaults_to_unknown(self):
        result = normalize_form_c_record({})
        assert result["name"] == "Unknown"


# ---------------------------------------------------------------------------
# classify helpers tests
# ---------------------------------------------------------------------------

class TestClassifyHelpers:
    def test_round_type_form_c(self):
        assert _classify_round_type("C") == "reg_cf"

    def test_round_type_form_c_u(self):
        assert _classify_round_type("C-U") == "reg_cf"

    def test_round_type_amendment(self):
        assert _classify_round_type("C/A") == "reg_cf_amendment"

    def test_round_type_annual_report(self):
        assert _classify_round_type("C-AR") == "reg_cf_annual_report"

    def test_round_type_termination(self):
        assert _classify_round_type("C-TR") == "reg_cf_termination"

    def test_round_type_none_defaults(self):
        assert _classify_round_type(None) == "reg_cf"

    def test_instrument_type_default(self):
        assert _classify_instrument_type("C") == "equity"
        assert _classify_instrument_type(None) == "equity"


# ---------------------------------------------------------------------------
# RateLimiter tests
# ---------------------------------------------------------------------------

class TestRateLimiter:
    def test_first_call_does_not_wait(self):
        limiter = _RateLimiter(min_interval=1.0)
        start = time.monotonic()
        limiter.wait()
        elapsed = time.monotonic() - start
        # First call should be nearly instant
        assert elapsed < 0.1

    def test_rapid_calls_are_throttled(self):
        limiter = _RateLimiter(min_interval=0.15)
        limiter.wait()  # first call
        start = time.monotonic()
        limiter.wait()  # second call should wait
        elapsed = time.monotonic() - start
        # Should have waited at least ~0.1 seconds
        assert elapsed >= 0.1


# ---------------------------------------------------------------------------
# ingest_form_c_batch tests
# ---------------------------------------------------------------------------

class TestIngestFormCBatch:
    def test_returns_zero_for_empty_list(self, mock_conn: MagicMock):
        assert ingest_form_c_batch(mock_conn, []) == 0
        mock_conn.commit.assert_not_called()

    def test_inserts_records(self, mock_conn: MagicMock):
        records = [
            {
                "name": "Test Corp",
                "country": "US",
                "sector": "tech",
                "source": "sec_edgar",
                "source_id": "123",
                "sic_code": "7372",
                "filing_date": "2023-01-15",
                "funding_target": 100000.0,
                "amount_raised": 50000.0,
            },
        ]
        count = ingest_form_c_batch(mock_conn, records)
        assert count == 1
        mock_conn.commit.assert_called_once()


# ---------------------------------------------------------------------------
# download_form_c_index tests (mocked HTTP)
# ---------------------------------------------------------------------------

class TestDownloadFormCIndex:
    def test_invalid_year_raises(self, tmp_path: Path):
        with pytest.raises(ValueError, match="Year must be"):
            download_form_c_index(1990, 1, tmp_path)

    def test_invalid_quarter_raises(self, tmp_path: Path):
        with pytest.raises(ValueError, match="Quarter must be"):
            download_form_c_index(2023, 5, tmp_path)

    def test_skips_if_file_exists(self, tmp_path: Path):
        existing = tmp_path / "company_2023_Q1.idx"
        existing.write_text("already here")

        result = download_form_c_index(2023, 1, tmp_path)
        assert result == existing

    @patch("startuplens.pipelines.sec_edgar._build_client")
    @patch("startuplens.pipelines.sec_edgar._rate_limiter")
    def test_downloads_and_saves(
        self, mock_limiter: MagicMock, mock_client_fn: MagicMock, tmp_path: Path
    ):
        mock_response = MagicMock()
        mock_response.text = "fake index content"
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.get.return_value = mock_response
        mock_client_fn.return_value = mock_client

        settings = MagicMock()
        settings.sec_user_agent = "Test Agent test@example.com"

        result = download_form_c_index(2023, 1, tmp_path, settings=settings)

        assert result.exists()
        assert result.read_text() == "fake index content"
        mock_limiter.wait.assert_called_once()


# ---------------------------------------------------------------------------
# run_sec_pipeline tests (mocked)
# ---------------------------------------------------------------------------

class TestRunSecPipeline:
    @patch("startuplens.pipelines.sec_edgar.ingest_form_c_batch", return_value=5)
    @patch("startuplens.pipelines.sec_edgar.parse_form_c_filings", return_value=[{"cik": "1"}])
    @patch("startuplens.pipelines.sec_edgar.download_form_c_index")
    @patch("startuplens.pipelines.sec_edgar._is_quarter_ingested", return_value=False)
    def test_processes_all_quarters(
        self,
        mock_ingested: MagicMock,
        mock_download: MagicMock,
        mock_parse: MagicMock,
        mock_ingest: MagicMock,
        tmp_path: Path,
    ):
        mock_download.return_value = tmp_path / "test.idx"
        conn = MagicMock()
        settings = MagicMock()

        summary = run_sec_pipeline(conn, settings, [2023], output_dir=tmp_path)

        assert summary["quarters_processed"] == 4
        assert summary["records_ingested"] == 20  # 5 per quarter x 4 quarters

    @patch("startuplens.pipelines.sec_edgar._is_quarter_ingested", return_value=True)
    def test_skips_ingested_quarters(self, mock_ingested: MagicMock, tmp_path: Path):
        conn = MagicMock()
        settings = MagicMock()

        summary = run_sec_pipeline(conn, settings, [2023], output_dir=tmp_path)

        assert summary["quarters_skipped"] == 4
        assert summary["quarters_processed"] == 0
        assert summary["records_ingested"] == 0


# ---------------------------------------------------------------------------
# derive_sec_outcomes tests
# ---------------------------------------------------------------------------


class TestDeriveSecOutcomes:
    @patch("startuplens.db.execute_query")
    def test_returns_count_from_db(self, mock_eq: MagicMock):
        conn = MagicMock()
        # First call is the INSERT ... SELECT (returns [])
        # Second call is the COUNT query
        mock_eq.side_effect = [[], [{"cnt": 42}]]

        result = derive_sec_outcomes(conn)
        assert result == 42
        conn.commit.assert_called_once()

    @patch("startuplens.db.execute_query")
    def test_returns_zero_when_no_matches(self, mock_eq: MagicMock):
        conn = MagicMock()
        mock_eq.side_effect = [[], [{"cnt": 0}]]

        result = derive_sec_outcomes(conn)
        assert result == 0

    @patch("startuplens.db.execute_query")
    def test_insert_query_contains_cross_reference(self, mock_eq: MagicMock):
        conn = MagicMock()
        mock_eq.side_effect = [[], [{"cnt": 5}]]

        derive_sec_outcomes(conn)

        insert_sql = mock_eq.call_args_list[0][0][1]
        assert "form_c" in insert_sql
        assert "form_d_ciks" in insert_sql
        assert "INSERT INTO crowdfunding_outcomes" in insert_sql
        assert "sec_cross_reference" in insert_sql
