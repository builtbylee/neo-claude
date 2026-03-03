#!/usr/bin/env python3
"""CLI script to quarantine holdout entities for the final backtest window."""

from __future__ import annotations

import structlog
import typer

from startuplens.backtest.holdout import get_holdout_summary, quarantine_holdout
from startuplens.config import get_settings
from startuplens.db import execute_query, get_connection, refresh_matview

logger = structlog.get_logger(__name__)
app = typer.Typer()


@app.command()
def main(
    window_label: str = typer.Argument(
        default="2023-2025", help="Holdout window label"
    ),
    test_start: str = typer.Option("2023-01-01", help="Test window start date"),
    test_end: str = typer.Option("2025-12-31", help="Test window end date"),
) -> None:
    """Quarantine entities that fall within the holdout test window."""
    settings = get_settings()
    conn = get_connection(settings)

    try:
        refresh_matview(conn)
        # Find entities with campaign dates in the holdout window
        rows = execute_query(
            conn,
            """
            SELECT DISTINCT tfw.entity_id::text AS entity_id
            FROM training_features_wide tfw
            WHERE tfw.as_of_date BETWEEN %s AND %s
            """,
            (test_start, test_end),
        )

        entity_ids = [r["entity_id"] for r in rows]
        logger.info("holdout_candidates", count=len(entity_ids), window=window_label)

        if entity_ids:
            inserted = quarantine_holdout(conn, entity_ids, window_label)
            conn.commit()
            logger.info("holdout_quarantined", inserted=inserted)

        # Show summary
        summary = get_holdout_summary(conn)
        for row in summary:
            logger.info(
                "holdout_window",
                window=row["holdout_window"],
                entities=row["entity_count"],
            )

    finally:
        conn.close()


if __name__ == "__main__":
    app()
