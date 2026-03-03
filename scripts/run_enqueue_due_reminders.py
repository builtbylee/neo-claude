#!/usr/bin/env python3
"""Queue alert_events for due deal reminders with dedupe keys."""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
import typer

from startuplens.config import get_settings
from startuplens.db import execute_query, get_connection

logger = structlog.get_logger(__name__)
app = typer.Typer()


@app.command()
def main(limit: int = typer.Option(200, help="Max reminders to queue per run")) -> None:
    settings = get_settings()
    conn = get_connection(settings)
    try:
        rows = execute_query(
            conn,
            """
            SELECT
              r.id::text AS reminder_id,
              r.deal_id::text AS deal_id,
              r.reminder_type,
              r.priority,
              r.due_at::text AS due_at,
              r.payload,
              d.company_name
            FROM deal_reminders r
            JOIN deal_pipeline_items d ON d.id = r.deal_id
            WHERE r.status = 'pending'
              AND r.due_at <= now()
            ORDER BY r.due_at ASC
            LIMIT %s
            """,
            (limit,),
        )
        queued = 0
        for row in rows:
            dedupe_key = f"reminder:{row['reminder_id']}"
            execute_query(
                conn,
                """
                INSERT INTO alert_events (
                  deal_id,
                  alert_type,
                  priority,
                  dedupe_key,
                  payload,
                  queued_at,
                  status
                )
                VALUES (
                  %s::uuid,
                  'task_due',
                  %s,
                  %s,
                  %s::jsonb,
                  now(),
                  'queued'
                )
                ON CONFLICT (dedupe_key) WHERE status IN ('queued', 'sent') DO NOTHING
                """,
                (
                    row["deal_id"],
                    row["priority"],
                    dedupe_key,
                    '{"source":"deal_reminders"}',
                ),
            )
            execute_query(
                conn,
                """
                UPDATE deal_reminders
                SET status = 'sent', updated_at = now()
                WHERE id = %s::uuid
                """,
                (row["reminder_id"],),
            )
            queued += 1
        conn.commit()
        logger.info(
            "due_reminders_enqueued",
            queued=queued,
            checked=len(rows),
            run_at=datetime.now(tz=UTC).isoformat(),
        )
    finally:
        conn.close()


if __name__ == "__main__":
    app()
