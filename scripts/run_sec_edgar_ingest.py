#!/usr/bin/env python3
"""CLI script to run the SEC EDGAR Form C ingestion pipeline."""

from __future__ import annotations

import structlog
import typer

from startuplens.config import get_settings
from startuplens.db import get_connection
from startuplens.pipelines.sec_edgar import derive_sec_outcomes, run_sec_pipeline

logger = structlog.get_logger(__name__)
app = typer.Typer()


@app.command()
def main(
    years: list[int] = typer.Argument(None, help="Years to ingest, e.g. 2021 2022 2023"),
    output_dir: str = typer.Option("data/sec_edgar", help="Directory for downloaded files"),
    derive_outcomes: bool = typer.Option(
        False, "--derive-outcomes", help="Derive outcome labels from Form C/D cross-reference"
    ),
) -> None:
    """Download and ingest SEC EDGAR Form C filings."""
    from pathlib import Path

    settings = get_settings()
    conn = get_connection(settings)

    try:
        if derive_outcomes:
            count = derive_sec_outcomes(conn)
            logger.info("outcomes_derived", count=count)
            return

        if not years:
            logger.error("no_years_specified")
            raise typer.BadParameter("Provide years to ingest, or use --derive-outcomes")

        summary = run_sec_pipeline(conn, settings, years, output_dir=Path(output_dir))
        logger.info("sec_edgar_ingest_complete", **summary)
    finally:
        conn.close()


if __name__ == "__main__":
    app()
