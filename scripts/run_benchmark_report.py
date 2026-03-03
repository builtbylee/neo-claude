#!/usr/bin/env python3
"""Generate model-vs-baseline benchmark summary from backtest_runs."""

from __future__ import annotations

from pathlib import Path

import structlog
import typer

from startuplens.config import get_settings
from startuplens.db import execute_query, get_connection

logger = structlog.get_logger(__name__)
app = typer.Typer()


@app.command()
def main(
    output: str = typer.Option(
        "data/reports/benchmark_summary.json",
        help="Output JSON path.",
    ),
) -> None:
    settings = get_settings()
    conn = get_connection(settings)
    try:
        rows = execute_query(
            conn,
            """
            SELECT run_date, model_family, metrics, pass_fail, all_passed
            FROM backtest_runs
            ORDER BY run_date DESC
            LIMIT 24
            """,
        )
    finally:
        conn.close()

    if not rows:
        raise typer.BadParameter("No backtest runs found.")

    import json

    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")
    logger.info("benchmark_report_written", output=str(out), runs=len(rows))


if __name__ == "__main__":
    app()
