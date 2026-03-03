#!/usr/bin/env python3
"""Sync deal pipeline records to/from Airtable (free CRM default)."""

from __future__ import annotations

import structlog
import typer

from startuplens.config import get_settings
from startuplens.db import execute_query, get_connection
from startuplens.integrations.airtable import (
    get_airtable_config,
    list_records,
    upsert_records,
)

logger = structlog.get_logger(__name__)
app = typer.Typer()


def _load_local_deals(conn, owner_email: str, limit: int = 200) -> list[dict]:
    return execute_query(
        conn,
        """
        SELECT
            id::text AS id,
            company_name,
            sector,
            country,
            stage_bucket,
            recommendation_class,
            conviction_score,
            status,
            priority,
            owner_email,
            next_action_date::text AS next_action_date,
            updated_at::text AS updated_at
        FROM deal_pipeline_items
        WHERE owner_email = %s
        ORDER BY updated_at DESC
        LIMIT %s
        """,
        (owner_email, limit),
    )


@app.command()
def main(
    owner_email: str = typer.Option(..., help="Pipeline owner email"),
    direction: str = typer.Option(
        "bidirectional",
        help="push, pull, or bidirectional",
    ),
) -> None:
    settings = get_settings()
    cfg = get_airtable_config(settings)
    if cfg is None:
        logger.error("airtable_not_configured")
        raise typer.Exit(code=2)

    conn = get_connection(settings)
    try:
        if direction in {"push", "bidirectional"}:
            local_deals = _load_local_deals(conn, owner_email)
            outbound = [
                {
                    "fields": {
                        "DealID": d["id"],
                        "Company": d["company_name"],
                        "Sector": d.get("sector"),
                        "Country": d.get("country"),
                        "Stage": d.get("stage_bucket"),
                        "Recommendation": d.get("recommendation_class"),
                        "ConvictionScore": d.get("conviction_score"),
                        "Status": d.get("status"),
                        "Priority": d.get("priority"),
                        "OwnerEmail": d.get("owner_email"),
                        "NextActionDate": d.get("next_action_date"),
                        "UpdatedAt": d.get("updated_at"),
                    },
                }
                for d in local_deals
            ]
            stats = upsert_records(cfg, outbound)
            logger.info("airtable_push_complete", **stats)

        if direction in {"pull", "bidirectional"}:
            records = list_records(cfg, max_records=200)
            pulled = 0
            for rec in records:
                f = rec.get("fields", {})
                deal_id = f.get("DealID")
                if not deal_id:
                    continue
                execute_query(
                    conn,
                    """
                    UPDATE deal_pipeline_items
                    SET
                      status = COALESCE(%s, status),
                      priority = COALESCE(%s, priority),
                      next_action_date = COALESCE(%s::date, next_action_date),
                      updated_at = now()
                    WHERE id = %s::uuid
                    """,
                    (
                        f.get("Status"),
                        f.get("Priority"),
                        f.get("NextActionDate"),
                        deal_id,
                    ),
                )
                pulled += 1
            conn.commit()
            logger.info("airtable_pull_complete", pulled=pulled)
    finally:
        conn.close()


if __name__ == "__main__":
    app()

