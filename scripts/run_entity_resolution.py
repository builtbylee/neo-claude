#!/usr/bin/env python3
"""CLI script to run entity resolution across all data sources."""

from __future__ import annotations

from pathlib import Path

import structlog
import typer

from startuplens.config import get_settings
from startuplens.db import execute_query, get_connection
from startuplens.entity_resolution.resolver import (
    bulk_create_entities,
    run_entity_resolution,
    run_probabilistic_pass,
)

logger = structlog.get_logger(__name__)
app = typer.Typer()


@app.command()
def main(
    source: str | None = typer.Option(
        None, "--source", help="Filter to a specific source (e.g. sec_edgar, sec_form_d)"
    ),
    bulk: bool = typer.Option(
        False, "--bulk", help="Use bulk insert (for large datasets like Form D)"
    ),
    run_probabilistic: bool = typer.Option(
        False, "--probabilistic", help="Also run probabilistic (dedupe) matching pass"
    ),
    dedupe_settings: Path | None = typer.Option(
        None, help="Path to saved dedupe model settings file"
    ),
    batch_size: int = typer.Option(500, help="Batch size for bulk insert mode"),
) -> None:
    """Run deterministic entity resolution, optionally followed by probabilistic pass."""
    settings = get_settings()
    conn = get_connection(settings)

    try:
        # Build query with optional source filter
        query = """
            SELECT
                c.id::text AS source_identifier,
                c.name,
                c.country,
                c.source
            FROM companies c
            LEFT JOIN entity_links el
                ON el.source = c.source AND el.source_identifier = c.id::text
            WHERE el.id IS NULL
        """
        params: tuple = ()
        if source:
            query += " AND c.source = %s"
            params = (source,)

        records = execute_query(conn, query, params)
        logger.info("unlinked_records_found", count=len(records), source=source or "all")

        if records:
            if bulk:
                stats = bulk_create_entities(conn, records, batch_size=batch_size)
            else:
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
