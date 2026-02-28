#!/usr/bin/env python3
"""CLI script to run the SEC EDGAR Form C ingestion pipeline."""

from __future__ import annotations

import typer
import structlog

from startuplens.config import get_settings
from startuplens.db import get_connection
from startuplens.pipelines.sec_edgar import run_sec_pipeline

logger = structlog.get_logger(__name__)
app = typer.Typer()


@app.command()
def main(
    years: list[int] = typer.Argument(help="Years to ingest, e.g. 2021 2022 2023"),
    output_dir: str = typer.Option("data/sec_edgar", help="Directory for downloaded files"),
) -> None:
    """Download and ingest SEC EDGAR Form C filings."""
    from pathlib import Path

    settings = get_settings()
    conn = get_connection(settings)

    try:
        summary = run_sec_pipeline(conn, settings, years, output_dir=Path(output_dir))
        logger.info("sec_edgar_ingest_complete", **summary)
    finally:
        conn.close()


if __name__ == "__main__":
    app()
