#!/usr/bin/env python3
"""CLI script to run the SEC EDGAR Form D ingestion pipeline."""

from __future__ import annotations

import structlog
import typer

from startuplens.config import get_settings
from startuplens.db import get_connection
from startuplens.pipelines.sec_form_d import cross_reference_sec_filings, run_form_d_pipeline

logger = structlog.get_logger(__name__)
app = typer.Typer()


@app.command()
def main(
    years: list[int] = typer.Argument(help="Years to ingest, e.g. 2021 2022 2023"),
    output_dir: str = typer.Option("data/sec_form_d", help="Directory for downloaded files"),
    cross_reference: bool = typer.Option(
        False, "--cross-reference", help="Run CIK cross-referencing after ingestion",
    ),
) -> None:
    """Download and ingest SEC EDGAR Form D filings."""
    from pathlib import Path

    settings = get_settings()
    conn = get_connection(settings)

    try:
        summary = run_form_d_pipeline(conn, settings, years, output_dir=Path(output_dir))
        logger.info("sec_form_d_ingest_complete", **summary)

        if cross_reference:
            xref = cross_reference_sec_filings(conn)
            conn.commit()
            logger.info("cik_cross_reference_complete", **xref)
    finally:
        conn.close()


if __name__ == "__main__":
    app()
