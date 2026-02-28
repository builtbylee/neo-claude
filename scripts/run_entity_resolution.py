#!/usr/bin/env python3
"""CLI script to run entity resolution across all data sources."""

from __future__ import annotations

from pathlib import Path

import structlog
import typer

from startuplens.config import get_settings
from startuplens.db import execute_query, get_connection
from startuplens.entity_resolution.resolver import run_entity_resolution, run_probabilistic_pass

logger = structlog.get_logger(__name__)
app = typer.Typer()


@app.command()
def main(
    run_probabilistic: bool = typer.Option(
        False, "--probabilistic", help="Also run probabilistic (dedupe) matching pass"
    ),
    dedupe_settings: Path | None = typer.Option(
        None, help="Path to saved dedupe model settings file"
    ),
) -> None:
    """Run deterministic entity resolution, optionally followed by probabilistic pass."""
    settings = get_settings()
    conn = get_connection(settings)

    try:
        # Gather unlinked records from companies table
        records = execute_query(
            conn,
            """
            SELECT
                c.id::text AS source_identifier,
                c.name,
                c.country,
                c.source
            FROM companies c
            LEFT JOIN entity_links el ON el.source = c.source AND el.source_identifier = c.id::text
            WHERE el.id IS NULL
            """,
        )

        logger.info("unlinked_records_found", count=len(records))

        if records:
            stats = run_entity_resolution(conn, records)
            conn.commit()
            logger.info("deterministic_resolution_complete", **stats)

        if run_probabilistic:
            prob_stats = run_probabilistic_pass(
                conn,
                settings_path=dedupe_settings,
            )
            conn.commit()
            logger.info("probabilistic_resolution_complete", **prob_stats)

    finally:
        conn.close()


if __name__ == "__main__":
    app()
