#!/usr/bin/env python3
"""CLI script to ingest SEC DERA Crowdfunding Offerings data."""

from __future__ import annotations

from pathlib import Path

import structlog
import typer

from startuplens.config import get_settings
from startuplens.db import get_connection
from startuplens.pipelines.sec_dera_cf import run_dera_cf_pipeline

logger = structlog.get_logger(__name__)
app = typer.Typer()


@app.command()
def main(
    years: str = typer.Option(
        "2017-2024",
        help="Year range to ingest (e.g., '2017-2024' or '2024').",
    ),
    output_dir: str = typer.Option(
        "data/sec_dera_cf",
        help="Directory for downloaded ZIP files.",
    ),
    max_workers: int = typer.Option(4, help="Concurrent download threads."),
) -> None:
    """Ingest SEC DERA Crowdfunding Offerings datasets."""
    # Parse year range
    if "-" in years:
        start, end = years.split("-", 1)
        year_list = list(range(int(start), int(end) + 1))
    else:
        year_list = [int(years)]

    settings = get_settings()
    conn = get_connection(settings)

    try:
        summary = run_dera_cf_pipeline(
            conn,
            settings,
            year_list,
            output_dir=Path(output_dir),
            max_workers=max_workers,
        )

        logger.info(
            "dera_cf_ingest_complete",
            quarters_processed=summary["quarters_processed"],
            quarters_skipped=summary["quarters_skipped"],
            records_ingested=summary["records_ingested"],
            errors=summary["errors"],
        )
    finally:
        conn.close()


if __name__ == "__main__":
    app()
