#!/usr/bin/env python3
"""Rebuild outcome label tiers with stricter evidence weighting.

Promotes/demotes label_quality_tier using explicit evidence rules:
  - UK Companies House hard statuses -> Tier 1
  - US terminated/withdrawn SEC filing statuses -> Tier 1
  - Active-but-indirect signals -> Tier 2
  - Unknown/insufficient -> Tier 3
"""

from __future__ import annotations

import structlog
import typer

from startuplens.config import get_settings
from startuplens.db import execute_query, get_connection
from startuplens.feature_store.labels import classify_uk_outcome

logger = structlog.get_logger(__name__)
app = typer.Typer()


@app.command()
def main() -> None:
    settings = get_settings()
    conn = get_connection(settings)
    try:
        # UK: Companies House status mapped to outcome + tier.
        uk_rows = execute_query(
            conn,
            """
            SELECT co.id::text AS co_id, c.current_status
            FROM crowdfunding_outcomes co
            JOIN companies c ON c.id = co.company_id
            WHERE c.source = 'companies_house'
            """,
        )
        uk_updated = 0
        for row in uk_rows:
            outcome, detail = classify_uk_outcome(row.get("current_status") or "unknown")
            tier = (
                1
                if outcome in {"failed", "trading"} and detail != "active_distress_signals"
                else 2
            )
            if outcome == "unknown":
                tier = 3
            execute_query(
                conn,
                """
                UPDATE crowdfunding_outcomes
                SET outcome = %s,
                    outcome_detail = %s,
                    label_quality_tier = %s
                WHERE id = %s::uuid
                """,
                (outcome, detail, tier, row["co_id"]),
            )
            uk_updated += 1

        # US: hard SEC-derived failure signals -> Tier 1.
        us_failed = execute_query(
            conn,
            """
            UPDATE crowdfunding_outcomes co
            SET label_quality_tier = 1
            FROM companies c
            WHERE co.company_id = c.id
              AND c.source IN ('sec_dera_cf', 'sec_edgar')
              AND co.outcome = 'failed'
            RETURNING co.id
            """,
        )
        us_trading = execute_query(
            conn,
            """
            UPDATE crowdfunding_outcomes co
            SET label_quality_tier = 2
            FROM companies c
            WHERE co.company_id = c.id
              AND c.source IN ('sec_dera_cf', 'sec_edgar')
              AND co.outcome = 'trading'
              AND co.label_quality_tier > 2
            RETURNING co.id
            """,
        )
        unknown = execute_query(
            conn,
            """
            UPDATE crowdfunding_outcomes
            SET label_quality_tier = 3
            WHERE outcome = 'unknown'
            RETURNING id
            """,
        )
        conn.commit()
        logger.info(
            "label_rebuild_complete",
            uk_updated=uk_updated,
            us_failed_tier1=len(us_failed),
            us_trading_tier2=len(us_trading),
            unknown_tier3=len(unknown),
        )
    finally:
        conn.close()


if __name__ == "__main__":
    app()
