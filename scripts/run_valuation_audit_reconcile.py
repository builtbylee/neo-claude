#!/usr/bin/env python3
"""Reconcile valuation scenario audits against realized outcomes.

Priority order for realized outcomes:
1) investments.outcome_multiple (invested positions with known outcomes)
2) crowdfunding_outcomes outcome proxy (failed=0.0, trading=1.0, exited=3.0)
"""

from __future__ import annotations

import structlog
import typer

from startuplens.config import get_settings
from startuplens.db import execute_query, get_connection

logger = structlog.get_logger(__name__)
app = typer.Typer()


@app.command()
def main(limit: int = typer.Option(2000, help="Max unresolved audits to reconcile")) -> None:
    settings = get_settings()
    conn = get_connection(settings)
    try:
        unresolved = execute_query(
            conn,
            """
            SELECT id, entity_id, company_id
            FROM valuation_scenario_audits
            WHERE realized_moic IS NULL
            ORDER BY created_at ASC
            LIMIT %s
            """,
            (limit,),
        )
        updated = 0

        for row in unresolved:
            audit_id = row["id"]
            entity_id = row.get("entity_id")
            company_id = row.get("company_id")

            realized_status = None
            realized_moic = None
            realized_at = None

            if entity_id:
                inv = execute_query(
                    conn,
                    """
                    SELECT current_status, outcome_multiple, outcome_date
                    FROM investments
                    WHERE entity_id = %s
                      AND outcome_multiple IS NOT NULL
                    ORDER BY outcome_date DESC NULLS LAST, created_at DESC
                    LIMIT 1
                    """,
                    (entity_id,),
                )
                if inv:
                    realized_status = inv[0].get("current_status") or "exited"
                    realized_moic = inv[0].get("outcome_multiple")
                    realized_at = inv[0].get("outcome_date")

            if realized_moic is None and company_id:
                crowd = execute_query(
                    conn,
                    """
                    SELECT outcome, outcome_date
                    FROM crowdfunding_outcomes
                    WHERE company_id = %s
                      AND outcome IN ('failed', 'trading', 'exited')
                    ORDER BY outcome_date DESC NULLS LAST, campaign_date DESC NULLS LAST
                    LIMIT 1
                    """,
                    (company_id,),
                )
                if crowd:
                    realized_status = crowd[0].get("outcome")
                    realized_at = crowd[0].get("outcome_date")
                    if realized_status == "failed":
                        realized_moic = 0.0
                    elif realized_status == "trading":
                        realized_moic = 1.0
                    elif realized_status == "exited":
                        realized_moic = 3.0

            if realized_moic is None:
                continue

            execute_query(
                conn,
                """
                UPDATE valuation_scenario_audits
                SET
                    realized_status = %s,
                    realized_moic = %s,
                    realized_at = %s,
                    calibration_error = CASE
                        WHEN base_moic IS NULL THEN NULL
                        ELSE ABS(base_moic - %s)
                    END
                WHERE id = %s
                """,
                (realized_status, realized_moic, realized_at, realized_moic, audit_id),
            )
            updated += 1

        conn.commit()
        logger.info("valuation_audit_reconcile_complete", scanned=len(unresolved), updated=updated)
    finally:
        conn.close()


if __name__ == "__main__":
    app()
