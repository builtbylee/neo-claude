#!/usr/bin/env python3
"""Build canonical transaction truth spine and apply analyst-grade valuation gates."""

from __future__ import annotations

import structlog
import typer

from startuplens.config import get_settings
from startuplens.db import execute_query, get_connection
from startuplens.pipelines.companies_house_snapshot import ingest_companies_house_snapshot
from startuplens.pipelines.transaction_truth import (
    apply_valuation_truth_gate,
    ingest_form_adv_investor_reference,
    ingest_late_stage_terms_from_edgar,
    ingest_official_traction_signals,
    ingest_round_spine_from_crowdfunding_outcomes,
    ingest_terms_from_form_c_texts,
    ingest_uk_private_round_spine,
    ingest_us_private_round_spine,
    reconcile_transaction_round_fields,
)

logger = structlog.get_logger(__name__)
app = typer.Typer()


@app.command()
def main(
    us_limit: int = typer.Option(250000, help="Max rounds to process from existing funding data."),
    us_country: str | None = typer.Option(
        "US",
        help="Optional country filter for funding-round spine (set empty for all).",
    ),
    outcome_limit: int = typer.Option(
        300000,
        help="Max rounds to backfill from crowdfunding_outcomes.",
    ),
    max_outcome_label_tier: int = typer.Option(
        3,
        help="Maximum crowdfunding outcome label tier to include (1-3).",
    ),
    uk_company_file: str | None = typer.Option(
        None,
        help="Optional file with one Companies House number per line for SH01 ingestion.",
    ),
    uk_auto_limit: int = typer.Option(
        5000,
        help="If uk_company_file not provided, auto-sample up to N UK company numbers from DB.",
    ),
    uk_snapshot_path: str | None = typer.Option(
        None,
        help="Optional Companies House monthly bulk snapshot CSV/ZIP path.",
    ),
    extract_form_c_terms: bool = typer.Option(
        True,
        help="Extract term hints from existing Form C narrative texts.",
    ),
    max_form_c_term_rounds: int = typer.Option(
        12000,
        help="Maximum rounds to enrich from Form C text terms.",
    ),
    extract_late_stage_terms: bool = typer.Option(
        True, help="Extract late-stage terms from EDGAR docs."
    ),
    max_late_stage_rounds: int = typer.Option(
        1500,
        help="Maximum transaction rounds for EDGAR late-stage extraction.",
    ),
    ingest_adv: bool = typer.Option(True, help="Ingest SEC Form ADV adviser disclosures."),
    enrich_signals: bool = typer.Option(
        True, help="Ingest official contracts/grants/patent signals."
    ),
) -> None:
    settings = get_settings()
    conn = get_connection(settings)

    try:
        us_stats = ingest_us_private_round_spine(conn, country=us_country, limit=us_limit)
        logger.info("transaction_truth_us_spine", **us_stats)

        outcome_stats = ingest_round_spine_from_crowdfunding_outcomes(
            conn,
            max_label_tier=max_outcome_label_tier,
            limit=outcome_limit,
        )
        logger.info("transaction_truth_outcome_spine", **outcome_stats)

        snapshot_stats = {"rows_read": 0, "companies_upserted": 0, "raw_records_upserted": 0}
        if uk_snapshot_path:
            from pathlib import Path

            snapshot_stats = ingest_companies_house_snapshot(conn, Path(uk_snapshot_path))
            logger.info("transaction_truth_uk_snapshot", **snapshot_stats)

        uk_stats = {"companies": 0, "rounds_upserted": 0, "facts_inserted": 0, "errors": 0}
        company_numbers: list[str] = []
        if uk_company_file:
            from pathlib import Path

            file_path = Path(uk_company_file)
            company_numbers = [
                line.strip()
                for line in file_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        elif uk_auto_limit > 0:
            rows = execute_query(
                conn,
                """
                SELECT source_id
                FROM companies
                WHERE source = 'companies_house'
                  AND source_id IS NOT NULL
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (uk_auto_limit,),
            )
            company_numbers = [
                str(r["source_id"]).strip()
                for r in rows
                if str(r["source_id"]).strip()
            ]

        if company_numbers:
            uk_stats = ingest_uk_private_round_spine(
                conn,
                settings,
                company_numbers=company_numbers,
            )
            logger.info("transaction_truth_uk_spine", **uk_stats)

        form_c_term_stats = {"rounds_processed": 0, "facts_inserted": 0}
        if extract_form_c_terms:
            form_c_term_stats = ingest_terms_from_form_c_texts(
                conn,
                max_rounds=max_form_c_term_rounds,
            )
            logger.info("transaction_truth_form_c_terms", **form_c_term_stats)

        late_stage_stats = {"rounds_processed": 0, "facts_inserted": 0, "errors": 0}
        if extract_late_stage_terms:
            late_stage_stats = ingest_late_stage_terms_from_edgar(
                conn,
                settings,
                max_rounds=max_late_stage_rounds,
            )
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
            outcomes=outcome_stats,
            snapshot=snapshot_stats,
            uk=uk_stats,
            form_c_terms=form_c_term_stats,
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
