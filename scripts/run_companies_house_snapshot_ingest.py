#!/usr/bin/env python3
"""Ingest Companies House monthly bulk snapshot into raw provenance + company status."""

from __future__ import annotations

from pathlib import Path

import structlog
import typer

from startuplens.config import get_settings
from startuplens.db import get_connection
from startuplens.pipelines.companies_house_snapshot import ingest_companies_house_snapshot

logger = structlog.get_logger(__name__)
app = typer.Typer()


@app.command()
def main(
    snapshot_path: Path = typer.Argument(help="Path to Companies House monthly snapshot CSV/ZIP."),
    limit: int | None = typer.Option(None, help="Optional max rows to ingest."),
) -> None:
    settings = get_settings()
    conn = get_connection(settings)
    try:
        stats = ingest_companies_house_snapshot(conn, snapshot_path, limit=limit)
        logger.info("companies_house_snapshot_ingest_complete", **stats)
    finally:
        conn.close()


if __name__ == "__main__":
    app()
