#!/usr/bin/env python3
"""CLI script to run the Companies House data ingestion pipeline."""

from __future__ import annotations

from pathlib import Path

import typer
import structlog

from startuplens.config import get_settings
from startuplens.db import get_connection
from startuplens.pipelines.companies_house import run_companies_house_pipeline

logger = structlog.get_logger(__name__)
app = typer.Typer()


@app.command()
def main(
    input_file: Path = typer.Argument(
        help="File with one company number per line"
    ),
    skip_verified: bool = typer.Option(True, help="Skip already-verified companies"),
) -> None:
    """Fetch and ingest Companies House data for a list of company numbers."""
    company_numbers = [
        line.strip() for line in input_file.read_text().splitlines() if line.strip()
    ]

    settings = get_settings()
    conn = get_connection(settings)

    try:
        stats = run_companies_house_pipeline(
            conn, settings, company_numbers, skip_verified=skip_verified
        )
        logger.info("companies_house_ingest_complete", **stats)
    finally:
        conn.close()


if __name__ == "__main__":
    app()
