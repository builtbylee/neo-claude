"""Academic dataset importers for crowdfunding outcome research.

Supports four published academic datasets:
  - Walthoff-Borm et al. (UK ECF outcomes)
  - Signori & Vismara (Italian/European ECF)
  - Kleinert et al. (German ECF)
  - KingsCrowd (US Reg CF ratings and outcomes)

All academic records receive label_quality_tier=1 (peer-reviewed methodology).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd
import structlog

from startuplens.feature_store.labels import assign_label_tier_academic

if TYPE_CHECKING:
    import psycopg

logger = structlog.get_logger(__name__)

# Expected CSV column names for each dataset (pre-normalization)
_WALTHOFF_BORM_COLUMNS: dict[str, str] = {
    "company_name": "name",
    "company": "name",
    "platform": "platform",
    "campaign_date": "campaign_date",
    "campaign_year": "campaign_year",
    "amount_raised": "amount_raised",
    "target": "funding_target",
    "funding_target": "funding_target",
    "equity_offered": "equity_offered",
    "equity_offered_pct": "equity_offered",
    "pre_money_valuation": "pre_money_valuation",
    "valuation": "pre_money_valuation",
    "investor_count": "investor_count",
    "investors": "investor_count",
    "outcome": "outcome",
    "status": "outcome",
    "outcome_date": "outcome_date",
    "sector": "sector",
    "industry": "sector",
    "country": "country",
    "age_months": "company_age_at_raise_months",
    "company_age": "company_age_at_raise_months",
    "had_revenue": "had_revenue",
    "has_revenue": "had_revenue",
    "revenue_at_raise": "revenue_at_raise",
    "eis_eligible": "eis_seis_eligible",
    "seis_eligible": "eis_seis_eligible",
    "founder_count": "founder_count",
    "founders": "founder_count",
    "prior_exits": "founder_prior_exits",
    "founder_prior_exits": "founder_prior_exits",
    "accelerator": "accelerator_alumni",
    "accelerator_alumni": "accelerator_alumni",
    "overfunding_ratio": "overfunding_ratio",
}

_SIGNORI_VISMARA_COLUMNS: dict[str, str] = {
    "company_name": "name",
    "firm_name": "name",
    "platform": "platform",
    "campaign_date": "campaign_date",
    "year": "campaign_year",
    "amount_raised": "amount_raised",
    "raised_eur": "amount_raised",
    "target_eur": "funding_target",
    "funding_target": "funding_target",
    "equity_pct": "equity_offered",
    "pre_money_eur": "pre_money_valuation",
    "investors": "investor_count",
    "n_investors": "investor_count",
    "outcome": "outcome",
    "status": "outcome",
    "sector": "sector",
    "country": "country",
    "age_at_campaign": "company_age_at_raise_months",
    "revenue_at_raise": "revenue_at_raise",
    "has_revenue": "had_revenue",
}

_KLEINERT_COLUMNS: dict[str, str] = {
    "company_name": "name",
    "startup_name": "name",
    "platform": "platform",
    "campaign_date": "campaign_date",
    "year": "campaign_year",
    "amount_raised": "amount_raised",
    "raised_eur": "amount_raised",
    "funding_goal": "funding_target",
    "target_eur": "funding_target",
    "equity_pct": "equity_offered",
    "valuation_eur": "pre_money_valuation",
    "investors": "investor_count",
    "outcome": "outcome",
    "failure": "failure_flag",
    "sector": "sector",
    "industry": "sector",
    "country": "country",
    "age_months": "company_age_at_raise_months",
    "team_size": "founder_count",
    "experience_years": "founder_domain_experience_years",
}

_KINGSCROWD_COLUMNS: dict[str, str] = {
    "company_name": "name",
    "company": "name",
    "platform": "platform",
    "campaign_date": "campaign_date",
    "date": "campaign_date",
    "amount_raised": "amount_raised",
    "total_raised": "amount_raised",
    "funding_target": "funding_target",
    "goal": "funding_target",
    "valuation": "pre_money_valuation",
    "pre_money_valuation": "pre_money_valuation",
    "investor_count": "investor_count",
    "investors": "investor_count",
    "outcome": "outcome",
    "rating": "kingscrowd_rating",
    "sector": "sector",
    "category": "sector",
    "country": "country",
    "revenue": "revenue_at_raise",
    "has_revenue": "had_revenue",
    "revenue_model": "revenue_model",
}


def _read_csv_normalized(
    csv_path: Path,
    column_map: dict[str, str],
) -> pd.DataFrame:
    """Read a CSV file and normalize column names using the given mapping.

    Args:
        csv_path: Path to the CSV file.
        column_map: Mapping from possible source column names to internal names.

    Returns:
        DataFrame with normalized column names.
    """
    df = pd.read_csv(csv_path, dtype=str)

    # Normalize source column names: lowercase, strip whitespace, replace spaces
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    # Apply column mapping
    rename_map = {}
    for src_col in df.columns:
        if src_col in column_map:
            rename_map[src_col] = column_map[src_col]

    df = df.rename(columns=rename_map)

    # Drop duplicate columns (keep first)
    df = df.loc[:, ~df.columns.duplicated()]

    return df


def _coerce_numeric(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Coerce specified columns to numeric, stripping currency symbols."""
    for col in columns:
        if col in df.columns:
            df[col] = (
                df[col]
                .astype(str)
                .str.replace(r"[$\u00a3\u20ac,]", "", regex=True)
                .str.strip()
            )
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _coerce_boolean(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Coerce specified columns to boolean."""
    true_vals = {"true", "1", "yes", "y", "t"}
    for col in columns:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip().str.lower().isin(true_vals)
    return df


def _coerce_integer(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Coerce specified columns to nullable integers."""
    for col in columns:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
            # Convert to nullable int (Int64) to preserve NaN
            df[col] = df[col].astype("Int64")
    return df


def _normalize_outcome(raw_outcome: str | None, failure_flag: str | None = None) -> str:
    """Map various outcome representations to our taxonomy.

    Returns one of: trading, failed, exited, unknown.
    """
    if failure_flag is not None:
        flag = str(failure_flag).strip().lower()
        if flag in ("1", "true", "yes", "failed"):
            return "failed"

    if raw_outcome is None or str(raw_outcome).strip().lower() in ("", "nan", "none"):
        return "unknown"

    outcome = str(raw_outcome).strip().lower()

    fail_keywords = ("fail", "dissolv", "liquidat", "bankrupt", "closed", "dead", "inactive")
    if any(k in outcome for k in fail_keywords):
        return "failed"

    exit_keywords = ("exit", "acqui", "ipo", "listed", "merged")
    if any(k in outcome for k in exit_keywords):
        return "exited"

    active_keywords = ("trad", "active", "operat", "alive", "running", "ongoing")
    if any(k in outcome for k in active_keywords):
        return "trading"

    return "unknown"


def _classify_stage_bucket(
    amount_raised: float | None,
    pre_money_valuation: float | None,
) -> str:
    """Classify a funding round into seed or early_growth.

    Simple heuristic: if amount raised < 1M or valuation < 5M, it's seed.
    """
    if amount_raised is not None and amount_raised >= 1_000_000:
        return "early_growth"
    if pre_money_valuation is not None and pre_money_valuation >= 5_000_000:
        return "early_growth"
    return "seed"


def _insert_academic_records(
    conn: psycopg.Connection,
    df: pd.DataFrame,
    data_source: str,
    default_country: str,
) -> int:
    """Insert normalized academic records into companies + crowdfunding_outcomes.

    Args:
        conn: Database connection.
        df: Normalized DataFrame with internal column names.
        data_source: Source identifier (e.g., "walthoff_borm").
        default_country: Default country code if not present in data.

    Returns:
        Number of records inserted.
    """
    label_tier = assign_label_tier_academic(data_source)
    inserted = 0
    numeric_cols = [
        "amount_raised", "funding_target", "equity_offered",
        "pre_money_valuation", "revenue_at_raise", "overfunding_ratio",
    ]
    bool_cols = [
        "had_revenue", "eis_seis_eligible", "founder_prior_exits",
        "accelerator_alumni",
    ]
    int_cols = [
        "investor_count", "company_age_at_raise_months",
        "founder_count", "founder_domain_experience_years",
    ]

    df = _coerce_numeric(df, numeric_cols)
    df = _coerce_boolean(df, bool_cols)
    df = _coerce_integer(df, int_cols)

    for _, row in df.iterrows():
        name = row.get("name")
        if not name or str(name).strip().lower() in ("", "nan", "none"):
            continue

        name = str(name).strip()
        country = str(row.get("country", default_country) or default_country).strip()
        sector = row.get("sector")
        if sector and str(sector).strip().lower() not in ("", "nan", "none"):
            sector = str(sector).strip()
        else:
            sector = None

        # Determine outcome
        outcome = _normalize_outcome(
            row.get("outcome"),
            row.get("failure_flag"),
        )

        amount_raised = row.get("amount_raised")
        pre_money = row.get("pre_money_valuation")
        stage = _classify_stage_bucket(
            float(amount_raised) if pd.notna(amount_raised) else None,
            float(pre_money) if pd.notna(pre_money) else None,
        )

        with conn.cursor() as cur:
            # Insert company
            cur.execute(
                """
                INSERT INTO companies (name, country, sector, source, source_id)
                VALUES (%(name)s, %(country)s, %(sector)s, %(source)s, %(source_id)s)
                RETURNING id
                """,
                {
                    "name": name,
                    "country": country,
                    "sector": sector,
                    "source": data_source,
                    "source_id": f"{data_source}_{inserted}",
                },
            )
            company_row = cur.fetchone()
            if company_row is None:
                continue
            company_id = company_row["id"]

            # Insert funding round
            round_date = row.get("campaign_date")
            if not round_date or str(round_date).strip().lower() in ("", "nan", "none"):
                campaign_year = row.get("campaign_year")
                if campaign_year and str(campaign_year).strip().lower() not in ("", "nan", "none"):
                    round_date = f"{str(campaign_year).strip()[:4]}-01-01"
                else:
                    round_date = None

            cur.execute(
                """
                INSERT INTO funding_rounds (
                    company_id, round_date, round_type, instrument_type,
                    amount_raised, pre_money_valuation, platform,
                    investor_count, overfunding_ratio, eis_seis_eligible, source
                ) VALUES (
                    %(company_id)s, %(round_date)s, %(round_type)s,
                    %(instrument_type)s, %(amount_raised)s,
                    %(pre_money_valuation)s, %(platform)s,
                    %(investor_count)s, %(overfunding_ratio)s,
                    %(eis_seis_eligible)s, %(source)s
                )
                """,
                {
                    "company_id": company_id,
                    "round_date": round_date,
                    "round_type": "seed_equity",
                    "instrument_type": "equity",
                    "amount_raised": _safe_float(amount_raised),
                    "pre_money_valuation": _safe_float(pre_money),
                    "platform": _safe_str(row.get("platform")),
                    "investor_count": _safe_int(row.get("investor_count")),
                    "overfunding_ratio": _safe_float(row.get("overfunding_ratio")),
                    "eis_seis_eligible": _safe_bool(row.get("eis_seis_eligible")),
                    "source": data_source,
                },
            )

            # Insert crowdfunding outcome
            cur.execute(
                """
                INSERT INTO crowdfunding_outcomes (
                    company_id, platform, campaign_date, funding_target,
                    amount_raised, overfunding_ratio, equity_offered,
                    pre_money_valuation, investor_count,
                    eis_seis_eligible, founder_count,
                    founder_domain_experience_years, founder_prior_exits,
                    had_revenue, revenue_at_raise, revenue_model,
                    company_age_at_raise_months, sector, country,
                    stage_bucket, outcome, outcome_detail, outcome_date,
                    label_quality_tier, data_source,
                    accelerator_alumni, prior_vc_backing
                ) VALUES (
                    %(company_id)s, %(platform)s, %(campaign_date)s,
                    %(funding_target)s, %(amount_raised)s, %(overfunding_ratio)s,
                    %(equity_offered)s, %(pre_money_valuation)s, %(investor_count)s,
                    %(eis_seis_eligible)s, %(founder_count)s,
                    %(founder_domain_experience_years)s, %(founder_prior_exits)s,
                    %(had_revenue)s, %(revenue_at_raise)s, %(revenue_model)s,
                    %(company_age_at_raise_months)s, %(sector)s, %(country)s,
                    %(stage_bucket)s, %(outcome)s, %(outcome_detail)s,
                    %(outcome_date)s, %(label_quality_tier)s, %(data_source)s,
                    %(accelerator_alumni)s, %(prior_vc_backing)s
                )
                """,
                {
                    "company_id": company_id,
                    "platform": _safe_str(row.get("platform")),
                    "campaign_date": round_date,
                    "funding_target": _safe_float(row.get("funding_target")),
                    "amount_raised": _safe_float(amount_raised),
                    "overfunding_ratio": _safe_float(row.get("overfunding_ratio")),
                    "equity_offered": _safe_float(row.get("equity_offered")),
                    "pre_money_valuation": _safe_float(pre_money),
                    "investor_count": _safe_int(row.get("investor_count")),
                    "eis_seis_eligible": _safe_bool(row.get("eis_seis_eligible")),
                    "founder_count": _safe_int(row.get("founder_count")),
                    "founder_domain_experience_years": _safe_int(
                        row.get("founder_domain_experience_years")
                    ),
                    "founder_prior_exits": _safe_bool(row.get("founder_prior_exits")),
                    "had_revenue": _safe_bool(row.get("had_revenue")),
                    "revenue_at_raise": _safe_float(row.get("revenue_at_raise")),
                    "revenue_model": _safe_str(row.get("revenue_model")),
                    "company_age_at_raise_months": _safe_int(
                        row.get("company_age_at_raise_months")
                    ),
                    "sector": sector,
                    "country": country,
                    "stage_bucket": stage,
                    "outcome": outcome,
                    "outcome_detail": _safe_str(row.get("outcome_detail")),
                    "outcome_date": _safe_str(row.get("outcome_date"))
                    if row.get("outcome_date")
                    and str(row.get("outcome_date")).strip().lower()
                    not in ("", "nan", "none")
                    else None,
                    "label_quality_tier": label_tier,
                    "data_source": data_source,
                    "accelerator_alumni": _safe_bool(row.get("accelerator_alumni")),
                    "prior_vc_backing": _safe_bool(row.get("prior_vc_backing")),
                },
            )

            inserted += 1

    conn.commit()
    logger.info("inserted_academic_records", inserted=inserted, source=data_source)
    return inserted


def _safe_float(val: Any) -> float | None:
    """Convert a value to float, returning None for missing/invalid."""
    if val is None:
        return None
    if isinstance(val, float) and pd.isna(val):
        return None
    try:
        result = float(val)
        return None if pd.isna(result) else result
    except (ValueError, TypeError):
        return None


def _safe_int(val: Any) -> int | None:
    """Convert a value to int, returning None for missing/invalid."""
    f = _safe_float(val)
    return int(f) if f is not None else None


def _safe_str(val: Any) -> str | None:
    """Convert a value to string, returning None for missing/invalid."""
    if val is None:
        return None
    s = str(val).strip()
    if s.lower() in ("", "nan", "none"):
        return None
    return s


def _safe_bool(val: Any) -> bool | None:
    """Convert a value to bool, returning None for missing/invalid."""
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    s = str(val).strip().lower()
    if s in ("true", "1", "yes", "y", "t"):
        return True
    if s in ("false", "0", "no", "n", "f"):
        return False
    return None


# --- Public importer functions ---


def import_walthoff_borm(conn: psycopg.Connection, csv_path: Path) -> int:
    """Import the Walthoff-Borm et al. UK ECF outcomes dataset.

    This dataset covers UK equity crowdfunding campaigns (primarily Seedrs and
    Crowdcube) with verified Companies House outcomes.

    Args:
        conn: Database connection.
        csv_path: Path to the Walthoff-Borm CSV file.

    Returns:
        Number of records imported.
    """
    logger.info("importing_walthoff_borm", path=str(csv_path))
    df = _read_csv_normalized(csv_path, _WALTHOFF_BORM_COLUMNS)
    return _insert_academic_records(conn, df, "walthoff_borm", default_country="GB")


def import_signori_vismara(conn: psycopg.Connection, csv_path: Path) -> int:
    """Import the Signori & Vismara Italian/European ECF outcomes dataset.

    Covers equity crowdfunding campaigns across European platforms with
    outcome tracking from national business registries.

    Args:
        conn: Database connection.
        csv_path: Path to the Signori-Vismara CSV file.

    Returns:
        Number of records imported.
    """
    logger.info("importing_signori_vismara", path=str(csv_path))
    df = _read_csv_normalized(csv_path, _SIGNORI_VISMARA_COLUMNS)
    return _insert_academic_records(conn, df, "signori_vismara", default_country="IT")


def import_kleinert(conn: psycopg.Connection, csv_path: Path) -> int:
    """Import the Kleinert et al. German ECF outcomes dataset.

    Covers German equity crowdfunding campaigns with outcome verification
    from the Handelsregister (German commercial register).

    Args:
        conn: Database connection.
        csv_path: Path to the Kleinert CSV file.

    Returns:
        Number of records imported.
    """
    logger.info("importing_kleinert", path=str(csv_path))
    df = _read_csv_normalized(csv_path, _KLEINERT_COLUMNS)
    return _insert_academic_records(conn, df, "kleinert", default_country="DE")


def import_kingscrowd(conn: psycopg.Connection, csv_path: Path) -> int:
    """Import the KingsCrowd US Reg CF ratings and outcomes dataset.

    Covers US Regulation CF campaigns with KingsCrowd analyst ratings
    and tracked outcomes.

    Args:
        conn: Database connection.
        csv_path: Path to the KingsCrowd CSV file.

    Returns:
        Number of records imported.
    """
    logger.info("importing_kingscrowd", path=str(csv_path))
    df = _read_csv_normalized(csv_path, _KINGSCROWD_COLUMNS)
    return _insert_academic_records(conn, df, "kingscrowd", default_country="US")


def run_academic_pipeline(
    conn: psycopg.Connection,
    data_dir: Path,
) -> dict[str, Any]:
    """Orchestrate import of all academic datasets found in data_dir.

    Looks for known filenames in the data directory and imports each one.
    Missing files are skipped with a warning (not all datasets may be available).

    Expected filenames:
      - walthoff_borm.csv
      - signori_vismara.csv
      - kleinert.csv
      - kingscrowd.csv

    Args:
        conn: Database connection.
        data_dir: Directory containing academic CSV files.

    Returns:
        Summary dict with per-dataset import counts.
    """
    importers: list[tuple[str, str, Any]] = [
        ("walthoff_borm", "walthoff_borm.csv", import_walthoff_borm),
        ("signori_vismara", "signori_vismara.csv", import_signori_vismara),
        ("kleinert", "kleinert.csv", import_kleinert),
        ("kingscrowd", "kingscrowd.csv", import_kingscrowd),
    ]

    summary: dict[str, Any] = {
        "datasets_imported": 0,
        "datasets_skipped": 0,
        "total_records": 0,
        "per_dataset": {},
        "errors": [],
    }

    for name, filename, importer_fn in importers:
        csv_path = data_dir / filename

        if not csv_path.exists():
            logger.warning("dataset_file_not_found", path=str(csv_path))
            summary["datasets_skipped"] += 1
            summary["per_dataset"][name] = {"status": "skipped", "reason": "file_not_found"}
            continue

        try:
            count = importer_fn(conn, csv_path)
            summary["datasets_imported"] += 1
            summary["total_records"] += count
            summary["per_dataset"][name] = {"status": "imported", "records": count}
            logger.info("dataset_imported", dataset=name, records=count)
        except Exception as e:
            error_msg = f"{name}: {e!s}"
            logger.warning("dataset_import_error", dataset=name, error=error_msg)
            summary["errors"].append(error_msg)
            summary["per_dataset"][name] = {"status": "error", "error": str(e)}

    logger.info(
        "academic_pipeline_complete",
        datasets_imported=summary["datasets_imported"],
        total_records=summary["total_records"],
        errors=len(summary["errors"]),
    )
    return summary
