"""Tests for the SEC EDGAR Form D pipeline."""

from __future__ import annotations

import zipfile
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from startuplens.pipelines.sec_form_d import (
    _classify_round_type_d,
    _read_tsv_from_zip,
    download_form_d_dataset,
    ingest_form_d_batch,
    normalize_form_d_record,
    parse_form_d_dataset,
    run_form_d_pipeline,
)

# Path to test fixtures
FIXTURES_DIR = Path(__file__).parent / "fixtures" / "form_d_sample"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_zip(tmp_path: Path) -> Path:
    """Create a ZIP file from the fixture TSV files."""
    zip_path = tmp_path / "form_d_2023_Q1.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for tsv_name in ("FORMDSUBMISSION.tsv", "ISSUERS.tsv", "OFFERING.tsv"):
            fixture_path = FIXTURES_DIR / tsv_name
            zf.write(fixture_path, tsv_name)
    return zip_path


@pytest.fixture()
def mock_conn() -> MagicMock:
    """Create a mock database connection."""
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value.__enter__ = Mock(return_value=cursor)
    conn.cursor.return_value.__exit__ = Mock(return_value=False)
    cursor.fetchone.return_value = {"id": "test-uuid-123"}
    # For bulk inserts: fetchall returns list of {id, source_id} dicts
    cursor.fetchall.return_value = [{"id": "test-uuid-123", "source_id": "123_q2023Q1"}]
    return conn


# ---------------------------------------------------------------------------
# _read_tsv_from_zip tests
# ---------------------------------------------------------------------------


class TestReadTsvFromZip:
    def test_reads_tsv_file(self, sample_zip: Path):
        with zipfile.ZipFile(sample_zip) as zf:
            rows = _read_tsv_from_zip(zf, "ISSUERS.tsv")
        assert len(rows) == 3
        assert rows[0]["ENTITYNAME"] == "ACME VENTURES INC"

    def test_normalizes_column_names_to_uppercase(self, sample_zip: Path):
        with zipfile.ZipFile(sample_zip) as zf:
            rows = _read_tsv_from_zip(zf, "ISSUERS.tsv")
        for row in rows:
            assert all(k == k.upper() for k in row)

    def test_returns_empty_for_missing_file(self, sample_zip: Path):
        with zipfile.ZipFile(sample_zip) as zf:
            rows = _read_tsv_from_zip(zf, "NONEXISTENT.tsv")
        assert rows == []

    def test_handles_latin1_encoding(self, tmp_path: Path):
        """TSV with Latin-1 characters should be decoded without error."""
        zip_path = tmp_path / "latin1.zip"
        content = "NAME\tVALUE\nCaf\xe9 Corp\t100\n".encode("latin-1")
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("TEST.tsv", content)
        with zipfile.ZipFile(zip_path) as zf:
            rows = _read_tsv_from_zip(zf, "TEST.tsv")
        assert len(rows) == 1
        assert "Caf" in rows[0]["NAME"]


# ---------------------------------------------------------------------------
# parse_form_d_dataset tests
# ---------------------------------------------------------------------------


class TestParseFormDDataset:
    def test_parses_all_issuers(self, sample_zip: Path):
        records = parse_form_d_dataset(sample_zip)
        assert len(records) == 3

    def test_joins_issuers_and_offerings(self, sample_zip: Path):
        records = parse_form_d_dataset(sample_zip)
        for rec in records:
            assert "ENTITYNAME" in rec
            # All 3 fixture records have matching offerings
            assert "TOTALAMOUNTSOLD" in rec

    def test_joins_submission_fields(self, sample_zip: Path):
        records = parse_form_d_dataset(sample_zip)
        for rec in records:
            assert "FILING_DATE" in rec

    def test_handles_missing_offering(self, tmp_path: Path):
        """Issuer without a matching offering should still be returned."""
        zip_path = tmp_path / "partial.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr(
                "FORMDSUBMISSION.tsv",
                "ACCESSIONNUMBER\tCIK\tDATEFILED\tSUBMISSIONTYPE\n"
                "0001234567-23-000001\t1234567\t2023-01-15\tD\n",
            )
            zf.writestr(
                "ISSUERS.tsv",
                "ACCESSIONNUMBER\tCIK\tENTITYNAME\n"
                "0001234567-23-000001\t1234567\tTEST CORP\n",
            )
            zf.writestr("OFFERINGS.tsv", "ACCESSIONNUMBER\tTOTALAMOUNTSOLD\n")
        records = parse_form_d_dataset(zip_path)
        assert len(records) == 1
        assert records[0]["ENTITYNAME"] == "TEST CORP"
        assert "TOTALAMOUNTSOLD" not in records[0]

    def test_empty_zip_returns_empty(self, tmp_path: Path):
        zip_path = tmp_path / "empty.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("FORMDSUBMISSION.tsv", "ACCESSIONNUMBER\tCIK\n")
            zf.writestr("ISSUERS.tsv", "ACCESSIONNUMBER\tCIK\tENTITYNAME\n")
            zf.writestr("OFFERINGS.tsv", "ACCESSIONNUMBER\tTOTALAMOUNTSOLD\n")
        records = parse_form_d_dataset(zip_path)
        assert records == []


# ---------------------------------------------------------------------------
# normalize_form_d_record tests
# ---------------------------------------------------------------------------


class TestNormalizeFormDRecord:
    def test_maps_field_names(self):
        raw = {
            "ENTITYNAME": "ACME INC",
            "CIK": "0001234567",
            "FILING_DATE": "15-JAN-2023",
        }
        result = normalize_form_d_record(raw)
        assert result["name"] == "ACME INC"
        assert result["source_id"] == "1234567"
        assert result["filing_date"] == "2023-01-15"

    def test_strips_leading_zeros_from_cik(self):
        raw = {"CIK": "0000001234"}
        result = normalize_form_d_record(raw)
        assert result["source_id"] == "1234"

    def test_all_zero_cik_becomes_zero(self):
        raw = {"CIK": "0000000000"}
        result = normalize_form_d_record(raw)
        assert result["source_id"] == "0"

    def test_defaults_country_to_us(self):
        result = normalize_form_d_record({})
        assert result["country"] == "US"

    def test_defaults_source_to_sec_form_d(self):
        result = normalize_form_d_record({})
        assert result["source"] == "sec_form_d"

    def test_coerces_numeric_amount_raised(self):
        raw = {"TOTALAMOUNTSOLD": "1,500,000"}
        result = normalize_form_d_record(raw)
        assert result["amount_raised"] == 1_500_000.0

    def test_handles_empty_numeric_string(self):
        raw = {"TOTALAMOUNTSOLD": ""}
        result = normalize_form_d_record(raw)
        assert result.get("amount_raised") is None

    def test_handles_indefinite_offering_amount(self):
        raw = {"TOTALOFFERINGAMOUNT": "Indefinite"}
        result = normalize_form_d_record(raw)
        assert result["funding_target"] is None

    def test_derives_founding_date_from_year(self):
        raw = {"YEAROFINC_VALUE_ENTERED": "2020"}
        result = normalize_form_d_record(raw)
        assert result["founding_date"] == date(2020, 1, 1)

    def test_missing_year_gives_none_founding_date(self):
        result = normalize_form_d_record({})
        assert result["founding_date"] is None

    def test_invalid_year_gives_none_founding_date(self):
        raw = {"YEAROFINC_VALUE_ENTERED": "NotAYear"}
        result = normalize_form_d_record(raw)
        assert result["founding_date"] is None

    def test_normalizes_sector_to_lowercase(self):
        raw = {"INDUSTRYGROUPTYPE": "Technology"}
        result = normalize_form_d_record(raw)
        assert result["sector"] == "technology"

    def test_empty_sector_becomes_none(self):
        raw = {"INDUSTRYGROUPTYPE": "  "}
        result = normalize_form_d_record(raw)
        assert result["sector"] is None

    def test_preserves_federal_exemptions(self):
        raw = {"FEDERALEXEMPTIONS_ITEMS_LIST": "06b,06c"}
        result = normalize_form_d_record(raw)
        assert result["federal_exemptions"] == "06b,06c"

    def test_missing_name_defaults_to_unknown(self):
        result = normalize_form_d_record({})
        assert result["name"] == "Unknown"

    def test_dollar_sign_in_amount(self):
        raw = {"TOTALAMOUNTSOLD": "$2,500,000"}
        result = normalize_form_d_record(raw)
        assert result["amount_raised"] == 2_500_000.0

    def test_dd_mon_yyyy_date_format(self):
        raw = {"FILING_DATE": "29-MAR-2024"}
        result = normalize_form_d_record(raw)
        assert result["filing_date"] == "2024-03-29"

    def test_iso_date_format_preserved(self):
        raw = {"FILING_DATE": "2024-03-29"}
        result = normalize_form_d_record(raw)
        assert result["filing_date"] == "2024-03-29"


# ---------------------------------------------------------------------------
# _classify_round_type_d tests
# ---------------------------------------------------------------------------


class TestClassifyRoundTypeD:
    def test_506b(self):
        assert _classify_round_type_d("06b") == "rule_506b"

    def test_506c(self):
        assert _classify_round_type_d("06c") == "rule_506c"

    def test_504(self):
        assert _classify_round_type_d("04") == "rule_504"

    def test_multiple_exemptions_prefers_506c(self):
        assert _classify_round_type_d("06b,06c") == "rule_506c"

    def test_none_defaults_to_reg_d(self):
        assert _classify_round_type_d(None) == "reg_d"

    def test_empty_string_defaults_to_reg_d(self):
        assert _classify_round_type_d("") == "reg_d"

    def test_unknown_exemption_defaults_to_reg_d(self):
        assert _classify_round_type_d("xyz") == "reg_d"

    def test_generic_506(self):
        assert _classify_round_type_d("06") == "rule_506"

    def test_section_4a5(self):
        assert _classify_round_type_d("4(a)(5)") == "section_4a5"


# ---------------------------------------------------------------------------
# download_form_d_dataset tests
# ---------------------------------------------------------------------------


class TestDownloadFormDDataset:
    def test_invalid_year_too_low_raises(self, tmp_path: Path):
        with pytest.raises(ValueError, match="Year must be"):
            download_form_d_dataset(2005, 1, tmp_path)

    def test_invalid_year_too_high_raises(self, tmp_path: Path):
        with pytest.raises(ValueError, match="Year must be"):
            download_form_d_dataset(2050, 1, tmp_path)

    def test_invalid_quarter_raises(self, tmp_path: Path):
        with pytest.raises(ValueError, match="Quarter must be"):
            download_form_d_dataset(2023, 5, tmp_path)

    def test_skips_if_file_exists(self, tmp_path: Path):
        existing = tmp_path / "form_d_2023_Q1.zip"
        existing.write_bytes(b"already here")
        result = download_form_d_dataset(2023, 1, tmp_path)
        assert result == existing

    @patch("startuplens.pipelines.sec_form_d._build_client")
    @patch("startuplens.pipelines.sec_form_d._rate_limiter")
    def test_downloads_and_saves(
        self, mock_limiter: MagicMock, mock_client_fn: MagicMock, tmp_path: Path,
    ):
        # Create a mock streaming response
        mock_response = MagicMock()
        mock_response.iter_bytes.return_value = [b"fake zip content"]
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.stream.return_value.__enter__ = Mock(return_value=mock_response)
        mock_client.stream.return_value.__exit__ = Mock(return_value=False)
        mock_client_fn.return_value = mock_client

        settings = MagicMock()
        settings.sec_user_agent = "Test Agent test@example.com"

        result = download_form_d_dataset(2023, 1, tmp_path, settings=settings)

        assert result.exists()
        assert result.read_bytes() == b"fake zip content"
        mock_limiter.wait.assert_called_once()


# ---------------------------------------------------------------------------
# ingest_form_d_batch tests
# ---------------------------------------------------------------------------


class TestIngestFormDBatch:
    def test_returns_zero_for_empty_list(self, mock_conn: MagicMock):
        assert ingest_form_d_batch(mock_conn, []) == 0
        mock_conn.commit.assert_not_called()

    def test_inserts_records(self, mock_conn: MagicMock):
        records = [
            {
                "name": "Test Corp",
                "country": "US",
                "sector": "tech",
                "source": "sec_form_d",
                "source_id": "123_q2023Q1",
                "filing_date": "2023-01-15",
                "funding_target": 5_000_000.0,
                "amount_raised": 1_500_000.0,
                "federal_exemptions": "06b",
            },
        ]
        count = ingest_form_d_batch(mock_conn, records)
        assert count == 1
        mock_conn.commit.assert_called()

    def test_skips_funding_round_when_no_amount(self, mock_conn: MagicMock):
        records = [
            {
                "name": "No Money Corp",
                "country": "US",
                "source": "sec_form_d",
                "source_id": "456_q2023Q1",
            },
        ]
        count = ingest_form_d_batch(mock_conn, records)
        assert count == 1
        # Only one execute call (company insert), not two (no funding round)
        cursor = mock_conn.cursor.return_value.__enter__.return_value
        assert cursor.execute.call_count == 1


# ---------------------------------------------------------------------------
# run_form_d_pipeline tests
# ---------------------------------------------------------------------------


class TestRunFormDPipeline:
    @patch("startuplens.db.get_connection")
    @patch("startuplens.pipelines.sec_form_d.ingest_form_d_batch", return_value=100)
    @patch(
        "startuplens.pipelines.sec_form_d.parse_form_d_dataset",
        return_value=[{"CIK": "1"}],
    )
    @patch("startuplens.pipelines.sec_form_d.download_form_d_dataset")
    @patch("startuplens.pipelines.sec_form_d._is_quarter_ingested_d", return_value=False)
    def test_processes_all_quarters(
        self,
        mock_ingested: MagicMock,
        mock_download: MagicMock,
        mock_parse: MagicMock,
        mock_ingest: MagicMock,
        mock_get_conn: MagicMock,
        tmp_path: Path,
    ):
        mock_download.return_value = tmp_path / "test.zip"
        mock_get_conn.return_value = MagicMock()
        settings = MagicMock()

        summary = run_form_d_pipeline(MagicMock(), settings, [2023], output_dir=tmp_path)

        assert summary["quarters_processed"] == 4
        assert summary["records_ingested"] == 400  # 100 per quarter x 4

    @patch("startuplens.db.get_connection")
    @patch("startuplens.pipelines.sec_form_d._is_quarter_ingested_d", return_value=True)
    def test_skips_ingested_quarters(
        self, mock_ingested: MagicMock, mock_get_conn: MagicMock, tmp_path: Path,
    ):
        mock_get_conn.return_value = MagicMock()
        settings = MagicMock()

        summary = run_form_d_pipeline(MagicMock(), settings, [2023], output_dir=tmp_path)

        assert summary["quarters_skipped"] == 4
        assert summary["quarters_processed"] == 0

    @patch("startuplens.db.get_connection")
    @patch("startuplens.pipelines.sec_form_d.download_form_d_dataset")
    @patch("startuplens.pipelines.sec_form_d._is_quarter_ingested_d", return_value=False)
    def test_handles_http_errors(
        self,
        mock_ingested: MagicMock,
        mock_download: MagicMock,
        mock_get_conn: MagicMock,
        tmp_path: Path,
    ):
        mock_download.side_effect = httpx.HTTPStatusError(
            "Not Found",
            request=MagicMock(),
            response=MagicMock(status_code=404),
        )
        mock_get_conn.return_value = MagicMock()
        settings = MagicMock()

        summary = run_form_d_pipeline(MagicMock(), settings, [2023], output_dir=tmp_path)

        assert summary["quarters_processed"] == 0
        assert len(summary["errors"]) == 4


# Need httpx for the error test
import httpx  # noqa: E402
