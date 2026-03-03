#!/usr/bin/env python3
"""Apply 12-month retention policy and purge stale high-risk text payloads."""

from __future__ import annotations

from datetime import date, timedelta

import structlog
import typer

from startuplens.config import get_settings
from startuplens.db import execute_query, get_connection

logger = structlog.get_logger(__name__)
app = typer.Typer()


@app.command()
def main() -> None:
    settings = get_settings()
    cutoff = date.today() - timedelta(days=settings.data_retention_days)

    conn = get_connection(settings)
    try:
        # Retain scoring outputs, purge raw long-form narrative fields older than policy.
        updated_eval = execute_query(
            conn,
            """
            UPDATE evaluations
            SET
              pitch_text = NULL,
              founder_qa_text = NULL,
              founder_content_text = NULL,
              notes = COALESCE(notes, '') || ' [retention purge applied]'
            WHERE created_at::date < %s
              AND (
                pitch_text IS NOT NULL
                OR founder_qa_text IS NOT NULL
                OR founder_content_text IS NOT NULL
              )
            RETURNING id
            """,
            (cutoff.isoformat(),),
        )

        deleted_comments = execute_query(
            conn,
            """
            DELETE FROM deal_comments
            WHERE created_at::date < %s
            RETURNING id
            """,
            (cutoff.isoformat(),),
        )

        conn.commit()
        logger.info(
            "retention_purge_complete",
            evaluations_redacted=len(updated_eval),
            comments_deleted=len(deleted_comments),
            cutoff=cutoff.isoformat(),
        )
    finally:
        conn.close()


if __name__ == "__main__":
    app()

