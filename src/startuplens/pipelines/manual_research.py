"""Manual research CSV import pipeline.

Imports hand-researched company and outcome data from structured CSV files.
Each row is a manually verified company record that gets inserted into the
companies + crowdfunding_outcomes tables.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import structlog

from startuplens.db import execute_many
from startuplens.feature_store.labels import assign_label_tier_manual

logger = structlog.get_logger(__name__)

REQUIRED_COLUMNS = [
    "company_name",
    "country",
    "outcome",
    "verified_against_registry",
]


def validate_csv(df: pd.DataFrame) -> list[str]:
    """Check that the CSV has all required columns. Returns list of missing columns."""
    return [col for col in REQUIRED_COLUMNS if col not in df.columns]


def normalize_manual_record(row: dict) -> dict:
    """Normalize a single manual research row."""
    verified = str(row.get("verified_against_registry", "")).lower() in (
        "true", "yes", "1",
    )
    label_tier = assign_label_tier_manual(verified_against_registry=verified)

    incorporation_date = row.get("incorporation_date")
    if isinstance(incorporation_date, str) and incorporation_date:
        incorporation_date = date.fromisoformat(incorporation_date)
    elif not isinstance(incorporation_date, date):
        incorporation_date = None

    return {
        "company_name": str(row["company_name"]).strip(),
        "country": str(row.get("country", "")).strip().upper() or "UK",
        "registration_number": str(row.get("registration_number", "")).strip() or None,
        "incorporation_date": incorporation_date,
        "sector": str(row.get("sector", "")).strip() or None,
        "outcome": str(row["outcome"]).strip().lower(),
        "outcome_detail": str(row.get("outcome_detail", "")).strip() or None,
        "verified_against_registry": verified,
        "label_tier": label_tier,
        "notes": str(row.get("notes", "")).strip() or None,
    }


def ingest_manual_batch(conn: Any, records: list[dict]) -> int:
    """Insert manually researched records into companies + crowdfunding_outcomes."""
    if not records:
        return 0

    company_rows = [
        (
            r["registration_number"],
            r["company_name"],
            r.get("incorporation_date"),
            r["country"],
            r.get("sector"),
        )
        for r in records
    ]

    execute_many(
        conn,
        """
        INSERT INTO companies (
            registration_number, legal_name, incorporation_date, country, sic_codes
        ) VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (registration_number) DO NOTHING
        """,
        company_rows,
    )

    outcome_rows = [
        (
            r["company_name"],
            r["outcome"],
            r.get("outcome_detail"),
            r["label_tier"],
            r.get("notes"),
        )
        for r in records
    ]

    return execute_many(
        conn,
        """
        INSERT INTO crowdfunding_outcomes (
            company_name, outcome, outcome_detail, label_quality_tier, notes
        ) VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
        """,
        outcome_rows,
    )


def run_manual_import(conn: Any, csv_path: Path) -> dict[str, int]:
    """Import a manual research CSV file.

    Returns
    -------
    dict
        Stats: {total_rows, valid, skipped, ingested}.
    """
    df = pd.read_csv(csv_path)
    stats = {"total_rows": len(df), "valid": 0, "skipped": 0, "ingested": 0}

    missing = validate_csv(df)
    if missing:
        logger.error("manual_csv_missing_columns", missing=missing, path=str(csv_path))
        return stats

    records = []
    for _, row in df.iterrows():
        try:
            normalized = normalize_manual_record(row.to_dict())
            records.append(normalized)
            stats["valid"] += 1
        except (KeyError, ValueError) as e:
            logger.warning("manual_row_skip", error=str(e))
            stats["skipped"] += 1

    stats["ingested"] = ingest_manual_batch(conn, records)
    logger.info("manual_import_complete", path=str(csv_path), **stats)
    return stats
