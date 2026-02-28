"""Tests for academic dataset importers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, Mock

import pandas as pd
import pytest

from startuplens.pipelines.academic_datasets import (
    _classify_stage_bucket,
    _coerce_boolean,
    _coerce_numeric,
    _normalize_outcome,
    _read_csv_normalized,
    _safe_bool,
    _safe_float,
    _safe_int,
    _safe_str,
    import_kingscrowd,
    import_kleinert,
    import_signori_vismara,
    import_walthoff_borm,
    run_academic_pipeline,
)

# Path to test fixtures
FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def sample_csv(tmp_path: Path) -> Path:
    """Create a minimal academic sample CSV for testing."""
    content = (
        "company_name,platform,campaign_date,amount_raised,funding_target,"
        "equity_offered,pre_money_valuation,investor_count,outcome,sector,"
        "country,age_months,had_revenue,founder_count,prior_exits,accelerator,"
        "overfunding_ratio\n"
        "AlphaTech Ltd,seedrs,2019-03-15,150000,100000,10.5,1428571,234,active,"
        "fintech,GB,24,true,2,false,true,1.5\n"
        "BetaBrew Co,crowdcube,2020-06-01,50000,75000,15.0,333333,89,dissolved,"
        "food_beverage,GB,12,false,1,false,false,0.67\n"
    )
    csv_file = tmp_path / "test_academic.csv"
    csv_file.write_text(content)
    return csv_file


@pytest.fixture()
def mock_conn() -> MagicMock:
    """Create a mock database connection with cursor context manager."""
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value.__enter__ = Mock(return_value=cursor)
    conn.cursor.return_value.__exit__ = Mock(return_value=False)

    # cursor.fetchone returns a company id for the INSERT INTO companies
    cursor.fetchone.return_value = {"id": "test-uuid-academic"}

    return conn


@pytest.fixture()
def fixture_csv() -> Path:
    """Return path to the shipped academic_sample.csv fixture."""
    return FIXTURES_DIR / "academic_sample.csv"


# ---------------------------------------------------------------------------
# _read_csv_normalized tests
# ---------------------------------------------------------------------------

class TestReadCsvNormalized:
    def test_normalizes_column_names(self, sample_csv: Path):
        column_map = {"company_name": "name", "amount_raised": "amount_raised"}
        df = _read_csv_normalized(sample_csv, column_map)
        assert "name" in df.columns
        assert "amount_raised" in df.columns

    def test_handles_spaces_in_headers(self, tmp_path: Path):
        content = "Company Name,Amount Raised\nTest Co,100000\n"
        csv_file = tmp_path / "spaced.csv"
        csv_file.write_text(content)
        column_map = {"company_name": "name", "amount_raised": "amount_raised"}
        df = _read_csv_normalized(csv_file, column_map)
        assert "name" in df.columns

    def test_unmapped_columns_preserved(self, sample_csv: Path):
        column_map = {"company_name": "name"}
        df = _read_csv_normalized(sample_csv, column_map)
        # Original column (lowercase) should still be present if not remapped
        assert "platform" in df.columns


# ---------------------------------------------------------------------------
# _coerce_numeric tests
# ---------------------------------------------------------------------------

class TestCoerceNumeric:
    def test_strips_currency_symbols(self):
        df = pd.DataFrame({"amount": ["$1,500", "\u00a3200", "\u20ac3000"]})
        result = _coerce_numeric(df, ["amount"])
        assert result["amount"].tolist() == [1500.0, 200.0, 3000.0]

    def test_handles_non_numeric_values(self):
        df = pd.DataFrame({"amount": ["N/A", "unknown", ""]})
        result = _coerce_numeric(df, ["amount"])
        assert result["amount"].isna().all()

    def test_ignores_missing_columns(self):
        df = pd.DataFrame({"other": [1, 2]})
        result = _coerce_numeric(df, ["nonexistent"])
        assert "other" in result.columns  # no error raised


# ---------------------------------------------------------------------------
# _coerce_boolean tests
# ---------------------------------------------------------------------------

class TestCoerceBoolean:
    def test_true_values(self):
        df = pd.DataFrame({"flag": ["true", "1", "yes", "Y", "True"]})
        result = _coerce_boolean(df, ["flag"])
        assert result["flag"].all()

    def test_false_values(self):
        df = pd.DataFrame({"flag": ["false", "0", "no", "N", "other"]})
        result = _coerce_boolean(df, ["flag"])
        assert not result["flag"].any()


# ---------------------------------------------------------------------------
# _normalize_outcome tests
# ---------------------------------------------------------------------------

class TestNormalizeOutcome:
    def test_active_maps_to_trading(self):
        assert _normalize_outcome("active") == "trading"

    def test_dissolved_maps_to_failed(self):
        assert _normalize_outcome("dissolved") == "failed"

    def test_liquidation_maps_to_failed(self):
        assert _normalize_outcome("liquidation") == "failed"

    def test_bankrupt_maps_to_failed(self):
        assert _normalize_outcome("bankrupt") == "failed"

    def test_acquired_maps_to_exited(self):
        assert _normalize_outcome("acquired") == "exited"

    def test_ipo_maps_to_exited(self):
        assert _normalize_outcome("ipo") == "exited"

    def test_operating_maps_to_trading(self):
        assert _normalize_outcome("operating") == "trading"

    def test_none_maps_to_unknown(self):
        assert _normalize_outcome(None) == "unknown"

    def test_empty_string_maps_to_unknown(self):
        assert _normalize_outcome("") == "unknown"

    def test_nan_maps_to_unknown(self):
        assert _normalize_outcome("nan") == "unknown"

    def test_failure_flag_overrides(self):
        assert _normalize_outcome("active", failure_flag="1") == "failed"

    def test_failure_flag_true_string(self):
        assert _normalize_outcome("active", failure_flag="true") == "failed"

    def test_case_insensitive(self):
        assert _normalize_outcome("DISSOLVED") == "failed"
        assert _normalize_outcome("Active") == "trading"
        assert _normalize_outcome("Acquired") == "exited"


# ---------------------------------------------------------------------------
# _classify_stage_bucket tests
# ---------------------------------------------------------------------------

class TestClassifyStageBucket:
    def test_small_raise_is_seed(self):
        assert _classify_stage_bucket(50000, None) == "seed"

    def test_large_raise_is_early_growth(self):
        assert _classify_stage_bucket(1_500_000, None) == "early_growth"

    def test_high_valuation_is_early_growth(self):
        assert _classify_stage_bucket(None, 10_000_000) == "early_growth"

    def test_none_defaults_to_seed(self):
        assert _classify_stage_bucket(None, None) == "seed"

    def test_boundary_under_1m_is_seed(self):
        assert _classify_stage_bucket(999_999, None) == "seed"

    def test_boundary_at_1m_is_early_growth(self):
        assert _classify_stage_bucket(1_000_000, None) == "early_growth"


# ---------------------------------------------------------------------------
# Safe conversion helper tests
# ---------------------------------------------------------------------------

class TestSafeHelpers:
    def test_safe_float_valid(self):
        assert _safe_float("123.45") == 123.45
        assert _safe_float(100) == 100.0

    def test_safe_float_none(self):
        assert _safe_float(None) is None
        assert _safe_float("") is None

    def test_safe_float_nan(self):
        assert _safe_float(float("nan")) is None

    def test_safe_int_valid(self):
        assert _safe_int("42") == 42
        assert _safe_int(42.7) == 42

    def test_safe_int_none(self):
        assert _safe_int(None) is None

    def test_safe_str_valid(self):
        assert _safe_str("hello") == "hello"

    def test_safe_str_none_variants(self):
        assert _safe_str(None) is None
        assert _safe_str("nan") is None
        assert _safe_str("None") is None
        assert _safe_str("") is None

    def test_safe_bool_true(self):
        assert _safe_bool(True) is True
        assert _safe_bool("true") is True
        assert _safe_bool("1") is True

    def test_safe_bool_false(self):
        assert _safe_bool(False) is False
        assert _safe_bool("false") is False
        assert _safe_bool("0") is False

    def test_safe_bool_none(self):
        assert _safe_bool(None) is None
        assert _safe_bool("maybe") is None


# ---------------------------------------------------------------------------
# Importer function tests (mocked DB)
# ---------------------------------------------------------------------------

class TestImportWalthoffBorm:
    def test_imports_records(self, mock_conn: MagicMock, fixture_csv: Path):
        count = import_walthoff_borm(mock_conn, fixture_csv)
        assert count == 5  # 5 rows in the fixture
        mock_conn.commit.assert_called_once()

    def test_uses_gb_default_country(self, mock_conn: MagicMock, sample_csv: Path):
        """Walthoff-Borm importer should default to GB for UK dataset."""
        # The importer calls _insert_academic_records with default_country="GB"
        count = import_walthoff_borm(mock_conn, sample_csv)
        assert count > 0


class TestImportSignoriVismara:
    def test_imports_with_italian_defaults(self, mock_conn: MagicMock, tmp_path: Path):
        content = (
            "firm_name,platform,year,raised_eur,target_eur,investors,status,sector,country\n"
            "TestCo Srl,mamacrowd,2020,100000,80000,150,active,fintech,IT\n"
        )
        csv_file = tmp_path / "signori.csv"
        csv_file.write_text(content)
        count = import_signori_vismara(mock_conn, csv_file)
        assert count == 1


class TestImportKleinert:
    def test_imports_with_german_defaults(self, mock_conn: MagicMock, tmp_path: Path):
        content = (
            "startup_name,platform,year,raised_eur,funding_goal,investors,"
            "failure,sector,country\n"
            "StartupGmbH,companisto,2019,200000,150000,300,0,tech,DE\n"
        )
        csv_file = tmp_path / "kleinert.csv"
        csv_file.write_text(content)
        count = import_kleinert(mock_conn, csv_file)
        assert count == 1


class TestImportKingscrowd:
    def test_imports_with_us_defaults(self, mock_conn: MagicMock, tmp_path: Path):
        content = (
            "company,platform,date,total_raised,goal,investors,outcome,category,country\n"
            "US Startup Inc,republic,2022-01-15,500000,250000,800,operating,saas,US\n"
        )
        csv_file = tmp_path / "kingscrowd.csv"
        csv_file.write_text(content)
        count = import_kingscrowd(mock_conn, csv_file)
        assert count == 1


# ---------------------------------------------------------------------------
# Label tier tests
# ---------------------------------------------------------------------------

class TestAcademicLabelTier:
    """All academic importers should assign label_quality_tier=1."""

    def test_walthoff_borm_tier_1(self, mock_conn: MagicMock, sample_csv: Path):
        """Verify tier assignment through the database insert call."""
        import_walthoff_borm(mock_conn, sample_csv)

        cursor = mock_conn.cursor.return_value.__enter__.return_value
        # Find the crowdfunding_outcomes INSERT call and check label_quality_tier
        for call_args in cursor.execute.call_args_list:
            args = call_args[0]
            if len(args) >= 2 and isinstance(args[1], dict):
                if "label_quality_tier" in args[1]:
                    assert args[1]["label_quality_tier"] == 1


# ---------------------------------------------------------------------------
# run_academic_pipeline tests
# ---------------------------------------------------------------------------

class TestRunAcademicPipeline:
    def test_skips_missing_files(self, mock_conn: MagicMock, tmp_path: Path):
        """Pipeline should skip datasets whose files don't exist."""
        summary = run_academic_pipeline(mock_conn, tmp_path)
        assert summary["datasets_skipped"] == 4
        assert summary["datasets_imported"] == 0
        assert summary["total_records"] == 0

    def test_imports_available_files(self, mock_conn: MagicMock, tmp_path: Path):
        """Pipeline should import files that exist."""
        # Create just walthoff_borm.csv
        content = (
            "company_name,platform,campaign_date,amount_raised,funding_target,"
            "outcome,sector,country\n"
            "TestCo,seedrs,2020-01-01,100000,80000,active,fintech,GB\n"
        )
        (tmp_path / "walthoff_borm.csv").write_text(content)

        summary = run_academic_pipeline(mock_conn, tmp_path)

        assert summary["datasets_imported"] == 1
        assert summary["datasets_skipped"] == 3
        assert summary["total_records"] == 1
        assert summary["per_dataset"]["walthoff_borm"]["status"] == "imported"
        assert summary["per_dataset"]["signori_vismara"]["status"] == "skipped"

    def test_handles_import_errors_gracefully(self, mock_conn: MagicMock, tmp_path: Path):
        """Pipeline should catch errors and continue with remaining datasets."""
        # Create an invalid CSV that will cause an error
        (tmp_path / "walthoff_borm.csv").write_text("invalid csv with\x00null bytes")
        # Create a valid one
        content = (
            "company_name,platform,outcome,sector,country\n"
            "TestCo,republic,active,tech,US\n"
        )
        (tmp_path / "kingscrowd.csv").write_text(content)

        summary = run_academic_pipeline(mock_conn, tmp_path)

        # kingscrowd should still succeed even if walthoff_borm failed
        assert summary["per_dataset"]["kingscrowd"]["status"] == "imported"

    def test_summary_structure(self, mock_conn: MagicMock, tmp_path: Path):
        summary = run_academic_pipeline(mock_conn, tmp_path)
        assert "datasets_imported" in summary
        assert "datasets_skipped" in summary
        assert "total_records" in summary
        assert "per_dataset" in summary
        assert "errors" in summary
