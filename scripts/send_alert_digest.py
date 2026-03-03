#!/usr/bin/env python3
"""Deliver queued alert events via email with dedupe and quiet-hours policy."""

from __future__ import annotations

import json

import structlog
import typer

from startuplens.config import get_settings
from startuplens.db import execute_query, get_connection
from startuplens.integrations.alerts import AlertMessage, send_via_resend, should_deliver_now

logger = structlog.get_logger(__name__)
app = typer.Typer()


@app.command()
def main(limit: int = typer.Option(50, help="Max queued alerts to process")) -> None:
    settings = get_settings()
    if not settings.alert_email_to:
        logger.error("alert_email_to_not_configured")
        raise typer.Exit(code=2)

    conn = get_connection(settings)
    try:
        queued = execute_query(
            conn,
            """
            SELECT id::text, alert_type, priority, payload, queued_at::text
            FROM alert_events
            WHERE status = 'queued'
            ORDER BY queued_at ASC
            LIMIT %s
            """,
            (limit,),
        )

        sent = 0
        skipped = 0
        failed = 0
        for alert in queued:
            if not should_deliver_now(settings, alert["priority"]):
                skipped += 1
                continue

            payload = alert.get("payload") or {}
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    payload = {"raw": payload}

            subject = f"[StartupLens:{alert['priority'].upper()}] {alert['alert_type']}"
            body = (
                f"Alert Type: {alert['alert_type']}\n"
                f"Priority: {alert['priority']}\n"
                f"Queued At: {alert['queued_at']}\n\n"
                f"Payload:\n{json.dumps(payload, indent=2)}"
            )
            delivered = send_via_resend(
                settings,
                AlertMessage(
                    to_email=settings.alert_email_to,
                    subject=subject,
                    body_text=body,
                ),
            )
            if delivered:
                execute_query(
                    conn,
                    "UPDATE alert_events SET status = 'sent', sent_at = now() WHERE id = %s::uuid",
                    (alert["id"],),
                )
                sent += 1
            else:
                execute_query(
                    conn,
                    """
                    UPDATE alert_events
                    SET status = 'failed', failure_reason = 'delivery_failed'
                    WHERE id = %s::uuid
                    """,
                    (alert["id"],),
                )
                failed += 1
        conn.commit()
        logger.info("alert_delivery_complete", sent=sent, skipped=skipped, failed=failed)
    finally:
        conn.close()


if __name__ == "__main__":
    app()

