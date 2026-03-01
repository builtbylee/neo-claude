#!/usr/bin/env python3
"""Derive outcome labels for crowdfunding_outcomes from SEC filing types.

Parses all downloaded DERA CF ZIPs to extract per-filing metadata
(CIK, accession_number, submission_type, filing_date), bulk-loads into
sec_cf_filings, then updates crowdfunding_outcomes.outcome via bulk SQL.

Labeling rules:
  - C-TR / C-TR-W → "failed" (terminated reporting)
  - C-W / C-U-W / C/A-W → "failed" (withdrawn)
  - C-AR / C-AR/A with filing in last 18 months → "trading"
  - C-U (progress update) in last 12 months → "trading"
  - Multiple filings spanning 2+ years → "trading" (sustained activity)
  - Everything else → "unknown"
"""

from __future__ import annotations

import csv
import io
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import structlog
import typer

from startuplens.config import get_settings
from startuplens.db import execute_query, get_connection

logger = structlog.get_logger(__name__)
app = typer.Typer()

_SUBMISSION_FILE = "FORM_C_SUBMISSION.tsv"


# ---------------------------------------------------------------------------
# Step 1: Parse ZIPs → filing records
# ---------------------------------------------------------------------------


def _parse_date(val: str | None) -> str | None:
    if not val or not val.strip():
        return None
    val = val.strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%m/%d/%Y", "%d-%b-%Y"):
        try:
            from datetime import datetime

            return datetime.strptime(val, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _parse_zip(zip_path: Path) -> list[dict]:
    """Extract (cik, accession_number, submission_type, filing_date, quarter)
    from a single DERA CF ZIP file.
    """
    # Derive quarter from filename: dera_cf_2024_Q4.zip
    stem = zip_path.stem  # dera_cf_2024_Q4
    parts = stem.split("_")
    year = parts[2] if len(parts) >= 4 else "0000"
    q = parts[3] if len(parts) >= 4 else "Q0"
    quarter = f"{year}{q}"  # e.g. "2024Q4"

    records: list[dict] = []
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            matching = [n for n in zf.namelist() if n.upper().endswith(_SUBMISSION_FILE.upper())]
            if not matching:
                return records

            raw = zf.read(matching[0])
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("latin-1")

            reader = csv.DictReader(io.StringIO(text), delimiter="\t")
            for row in reader:
                cik = (row.get("CIK", "") or "").strip().lstrip("0") or "0"
                acc = (row.get("ACCESSION_NUMBER", "") or "").strip()
                sub_type = (row.get("SUBMISSION_TYPE", "") or "").strip()
                filing_date = _parse_date(row.get("FILING_DATE"))

                if acc and sub_type:
                    records.append({
                        "cik": cik,
                        "accession_number": acc,
                        "submission_type": sub_type,
                        "filing_date": filing_date,
                        "quarter": quarter,
                    })
    except Exception:
        logger.warning("zip_parse_error", path=str(zip_path))

    return records


def parse_all_zips(zip_dir: Path, max_workers: int = 8) -> list[dict]:
    """Parse all DERA CF ZIPs in parallel. Returns all filing records."""
    zips = sorted(zip_dir.glob("dera_cf_*.zip"))
    logger.info("parsing_zips", count=len(zips))

    all_records: list[dict] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_parse_zip, z): z for z in zips}
        for future in as_completed(futures):
            records = future.result()
            all_records.extend(records)

    logger.info("parsed_all_zips", total_filings=len(all_records))
    return all_records


# ---------------------------------------------------------------------------
# Step 2: Bulk load into sec_cf_filings
# ---------------------------------------------------------------------------


def bulk_load_filings(conn, records: list[dict]) -> int:
    """Bulk INSERT filing records into sec_cf_filings. Returns count inserted."""
    if not records:
        return 0

    chunk_size = 1000
    inserted = 0

    for i in range(0, len(records), chunk_size):
        chunk = records[i : i + chunk_size]

        # Deduplicate by accession_number within chunk
        seen: dict[str, dict] = {}
        for r in chunk:
            seen[r["accession_number"]] = r
        deduped = list(seen.values())

        placeholders = ", ".join(["(%s, %s, %s, %s, %s)"] * len(deduped))
        values = []
        for r in deduped:
            values.extend([
                r["cik"],
                r["accession_number"],
                r["submission_type"],
                r["filing_date"],
                r["quarter"],
            ])

        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO sec_cf_filings
                    (cik, accession_number, submission_type, filing_date, quarter)
                VALUES {placeholders}
                ON CONFLICT (accession_number) DO NOTHING
                """,
                values,
            )
            inserted += cur.rowcount

    conn.commit()
    logger.info("loaded_filings", inserted=inserted)
    return inserted


# ---------------------------------------------------------------------------
# Step 3: Derive outcome labels via bulk SQL
# ---------------------------------------------------------------------------


def label_outcomes(conn) -> dict[str, int]:
    """Update crowdfunding_outcomes.outcome using sec_cf_filings data.

    Returns counts of each label applied.
    """
    stats: dict[str, int] = {}
    cutoff_18m = (date.today() - timedelta(days=18 * 30)).isoformat()
    cutoff_12m = (date.today() - timedelta(days=12 * 30)).isoformat()

    # Label 1: C-TR or withdrawal → "failed"
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH failed_ciks AS (
                SELECT DISTINCT cik
                FROM sec_cf_filings
                WHERE submission_type IN ('C-TR', 'C-TR-W', 'C-W', 'C-U-W', 'C/A-W')
            )
            UPDATE crowdfunding_outcomes co
            SET outcome = 'failed',
                outcome_detail = 'terminated_or_withdrawn',
                label_quality_tier = 2
            FROM companies c
            JOIN failed_ciks fc ON fc.cik = SPLIT_PART(c.source_id, '_q', 1)
            WHERE co.company_id = c.id
              AND c.source = 'sec_dera_cf'
              AND co.outcome = 'unknown'
            """,
        )
        stats["failed"] = cur.rowcount

    # Label 2: C-AR filing within last 18 months → "trading"
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH active_ciks AS (
                SELECT DISTINCT cik
                FROM sec_cf_filings
                WHERE submission_type IN ('C-AR', 'C-AR/A')
                  AND filing_date >= %s
            )
            UPDATE crowdfunding_outcomes co
            SET outcome = 'trading',
                outcome_detail = 'annual_report_filed',
                label_quality_tier = 2
            FROM companies c
            JOIN active_ciks ac ON ac.cik = SPLIT_PART(c.source_id, '_q', 1)
            WHERE co.company_id = c.id
              AND c.source = 'sec_dera_cf'
              AND co.outcome = 'unknown'
            """,
            (cutoff_18m,),
        )
        stats["trading_ar"] = cur.rowcount

    # Label 3: C-U (progress update) within last 12 months → "trading"
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH progress_ciks AS (
                SELECT DISTINCT cik
                FROM sec_cf_filings
                WHERE submission_type = 'C-U'
                  AND filing_date >= %s
            )
            UPDATE crowdfunding_outcomes co
            SET outcome = 'trading',
                outcome_detail = 'progress_update_filed',
                label_quality_tier = 2
            FROM companies c
            JOIN progress_ciks pc ON pc.cik = SPLIT_PART(c.source_id, '_q', 1)
            WHERE co.company_id = c.id
              AND c.source = 'sec_dera_cf'
              AND co.outcome = 'unknown'
            """,
            (cutoff_12m,),
        )
        stats["trading_progress"] = cur.rowcount

    # Label 4: Sustained activity — filings spanning 2+ years → "trading"
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH sustained_ciks AS (
                SELECT cik
                FROM sec_cf_filings
                WHERE filing_date IS NOT NULL
                GROUP BY cik
                HAVING MAX(filing_date) - MIN(filing_date) > 730
            )
            UPDATE crowdfunding_outcomes co
            SET outcome = 'trading',
                outcome_detail = 'sustained_filing_activity',
                label_quality_tier = 3
            FROM companies c
            JOIN sustained_ciks sc ON sc.cik = SPLIT_PART(c.source_id, '_q', 1)
            WHERE co.company_id = c.id
              AND c.source = 'sec_dera_cf'
              AND co.outcome = 'unknown'
            """,
        )
        stats["trading_sustained"] = cur.rowcount

    conn.commit()

    # Summary
    summary = execute_query(
        conn,
        """
        SELECT outcome, COUNT(*) AS cnt
        FROM crowdfunding_outcomes
        GROUP BY outcome
        ORDER BY cnt DESC
        """,
    )
    for row in summary:
        logger.info("outcome_distribution", outcome=row["outcome"], count=row["cnt"])

    logger.info("labeling_complete", **stats)
    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@app.command()
def main(
    zip_dir: str = typer.Option("data/sec_dera_cf", help="Directory with DERA CF ZIPs"),
    max_workers: int = typer.Option(8, help="Parallel workers for ZIP parsing"),
) -> None:
    """Parse DERA CF ZIPs and derive outcome labels for crowdfunding_outcomes."""
    settings = get_settings()
    conn = get_connection(settings)

    try:
        # Step 1: Parse all ZIPs
        records = parse_all_zips(Path(zip_dir), max_workers=max_workers)

        # Step 2: Bulk load
        loaded = bulk_load_filings(conn, records)
        logger.info("filings_loaded", count=loaded)

        # Step 3: Label outcomes
        stats = label_outcomes(conn)
        logger.info("outcome_labeling_complete", **stats)

    finally:
        conn.close()


if __name__ == "__main__":
    app()
