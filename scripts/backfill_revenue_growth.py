#!/usr/bin/env python3
"""Backfill revenue_growth_yoy in financial_data from DERA CF raw data.

Downloads each quarterly DERA CF ZIP, extracts prior/current fiscal year
revenue, computes growth rate, and UPDATEs existing financial_data rows.
"""

from __future__ import annotations

import structlog
import typer

from startuplens.config import get_settings
from startuplens.db import get_connection
from startuplens.pipelines.sec_dera_cf import (
    download_dera_cf_dataset,
    normalize_dera_cf_record,
    parse_dera_cf_dataset,
)

logger = structlog.get_logger(__name__)
app = typer.Typer()

# Quarters available in DERA CF dataset
_START_YEAR, _START_Q = 2017, 1
_END_YEAR, _END_Q = 2025, 3


@app.command()
def main(
    data_dir: str = typer.Option("data/dera_cf", help="Directory for downloaded ZIPs"),
) -> None:
    """Backfill revenue_growth_yoy from DERA CF prior/current year data."""
    from pathlib import Path

    settings = get_settings()
    conn = get_connection(settings)
    output_dir = Path(data_dir)
    total_updated = 0

    try:
        year, quarter = _START_YEAR, _START_Q
        while (year, quarter) <= (_END_YEAR, _END_Q):
            try:
                zip_path = download_dera_cf_dataset(
                    year, quarter, output_dir, settings=settings,
                )
            except Exception as e:
                logger.warning("download_failed", year=year, quarter=quarter, error=str(e)[:100])
                quarter += 1
                if quarter > 4:
                    quarter = 1
                    year += 1
                continue

            raw_records = parse_dera_cf_dataset(zip_path)
            normalized = [normalize_dera_cf_record(r) for r in raw_records]

            # Add source_id (same logic as pipeline ingest)
            for rec in normalized:
                cik = rec.get("cik", "0")
                rec["source_id"] = f"{cik}_q{year}Q{quarter}"

            # Compute growth and build update batch
            updates: list[tuple[float, str, str]] = []
            for rec in normalized:
                rev_recent = rec.get("revenue_recent")
                rev_prior = rec.get("revenue_prior")
                if rev_recent is None or rev_prior is None or rev_prior == 0:
                    continue
                growth = (rev_recent - rev_prior) / abs(rev_prior)
                source_id = rec.get("source_id")
                filing_date = rec.get("filing_date")
                if source_id and filing_date:
                    updates.append((growth, source_id, filing_date))

            if updates:
                # Batch UPDATE via company source_id lookup
                with conn.cursor() as cur:
                    for growth, source_id, filing_date in updates:
                        cur.execute(
                            """
                            UPDATE financial_data fd
                            SET revenue_growth_yoy = %s
                            FROM companies c
                            WHERE fd.company_id = c.id
                              AND c.source = 'sec_dera_cf'
                              AND c.source_id = %s
                              AND fd.period_end_date = %s::date
                              AND fd.revenue_growth_yoy IS NULL
                            """,
                            (growth, source_id, filing_date),
                        )
                conn.commit()
                total_updated += len(updates)

            logger.info(
                "processed_quarter",
                year=year, quarter=quarter,
                records=len(normalized), updates=len(updates),
            )

            quarter += 1
            if quarter > 4:
                quarter = 1
                year += 1

        logger.info("backfill_complete", total_updated=total_updated)

        # Show distribution
        from startuplens.db import execute_query

        stats = execute_query(conn, """
            SELECT
                COUNT(*) as total,
                COUNT(revenue_growth_yoy) as has_growth,
                ROUND(AVG(revenue_growth_yoy)::numeric, 3) as avg_growth,
                ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (
                    ORDER BY revenue_growth_yoy
                )::numeric, 3) as median_growth
            FROM financial_data
            WHERE source_filing = 'sec_dera_cf'
        """)
        for row in stats:
            logger.info("growth_stats", **row)

    finally:
        conn.close()


if __name__ == "__main__":
    app()
