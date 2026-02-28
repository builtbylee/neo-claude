#!/usr/bin/env python3
"""CLI script to import academic datasets."""

from __future__ import annotations

from pathlib import Path

import typer
import structlog

from startuplens.config import get_settings
from startuplens.db import get_connection
from startuplens.pipelines.academic_datasets import run_academic_pipeline

logger = structlog.get_logger(__name__)
app = typer.Typer()


@app.command()
def main(
    data_dir: Path = typer.Argument(
        help="Directory containing academic dataset CSV files"
    ),
) -> None:
    """Import academic datasets (Walthoff-Borm, Signori, Kleinert, KingsCrowd)."""
    settings = get_settings()
    conn = get_connection(settings)

    try:
        summary = run_academic_pipeline(conn, data_dir)
        logger.info("academic_ingest_complete", **summary)
    finally:
        conn.close()


if __name__ == "__main__":
    app()
