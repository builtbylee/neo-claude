#!/usr/bin/env python3
"""Build canonical transaction truth spine and apply analyst-grade valuation gates."""

from __future__ import annotations

import structlog
import typer

from startuplens.config import get_settings
from startuplens.db import get_connection
from startuplens.pipelines.companies_house_snapshot import ingest_companies_house_snapshot
from startuplens.pipelines.transaction_truth import (
    apply_valuation_truth_gate,
    ingest_form_adv_investor_reference,
    ingest_late_stage_terms_from_edgar,
    ingest_official_traction_signals,
    ingest_uk_private_round_spine,
    ingest_us_private_round_spine,
    reconcile_transaction_round_fields,
)

logger = structlog.get_logger(__name__)
app = typer.Typer()


@app.command()
def main(
    us_limit: int = typer.Option(200000, help="Max US rounds to process from existing data."),
    uk_company_file: str | None = typer.Option(
        None,
        help="Optional file with one Companies House number per line for SH01 ingestion.",
    ),
    uk_snapshot_path: str | None = typer.Option(
        None,
        help="Optional Companies House monthly bulk snapshot CSV/ZIP path.",
    ),
    extract_late_stage_terms: bool = typer.Option(
        True, help="Extract late-stage terms from EDGAR docs."
    ),
    ingest_adv: bool = typer.Option(True, help="Ingest SEC Form ADV adviser disclosures."),
    enrich_signals: bool = typer.Option(
        True, help="Ingest official contracts/grants/patent signals."
    ),
) -> None:
    settings = get_settings()
    conn = get_connection(settings)

    try:
        us_stats = ingest_us_private_round_spine(conn, limit=us_limit)
        logger.info("transaction_truth_us_spine", **us_stats)

        snapshot_stats = {"rows_read": 0, "companies_upserted": 0, "raw_records_upserted": 0}
        if uk_snapshot_path:
            from pathlib import Path

            snapshot_stats = ingest_companies_house_snapshot(conn, Path(uk_snapshot_path))
            logger.info("transaction_truth_uk_snapshot", **snapshot_stats)

        uk_stats = {"companies": 0, "rounds_upserted": 0, "facts_inserted": 0, "errors": 0}
        if uk_company_file:
            from pathlib import Path

            file_path = Path(uk_company_file)
            company_numbers = [
                line.strip()
                for line in file_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            uk_stats = ingest_uk_private_round_spine(
                conn,
                settings,
                company_numbers=company_numbers,
            )
            logger.info("transaction_truth_uk_spine", **uk_stats)

        late_stage_stats = {"rounds_processed": 0, "facts_inserted": 0, "errors": 0}
        if extract_late_stage_terms:
            late_stage_stats = ingest_late_stage_terms_from_edgar(conn, settings)
            logger.info("transaction_truth_late_stage_terms", **late_stage_stats)

        reconcile_stats = reconcile_transaction_round_fields(conn)
        logger.info("transaction_truth_reconciled", **reconcile_stats)

        gate_stats = apply_valuation_truth_gate(conn)
        logger.info("transaction_truth_gate", **gate_stats)

        adv_stats = {"inserted": 0, "linked_rounds": 0, "scanned": 0}
        if ingest_adv:
            adv_stats = ingest_form_adv_investor_reference(conn, settings)
            logger.info("transaction_truth_adv", **adv_stats)

        signal_stats = {"signals_inserted": 0, "errors": 0, "companies_scanned": 0}
        if enrich_signals:
            signal_stats = ingest_official_traction_signals(conn, settings)
            logger.info("transaction_truth_official_signals", **signal_stats)

        logger.info(
            "transaction_truth_pipeline_complete",
            us=us_stats,
            snapshot=snapshot_stats,
            uk=uk_stats,
            late_stage=late_stage_stats,
            reconcile=reconcile_stats,
            gate=gate_stats,
            adv=adv_stats,
            signals=signal_stats,
        )
    finally:
        conn.close()


if __name__ == "__main__":
    app()
