#!/usr/bin/env python3
"""Run backtests for all stage-country families and write a summary report."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import structlog
import typer

from startuplens.config import get_settings
from startuplens.db import execute_query, get_connection

logger = structlog.get_logger(__name__)
app = typer.Typer()

FAMILIES = ["US_Seed", "US_EarlyGrowth", "UK_Seed", "UK_EarlyGrowth"]


@app.command()
def main(
    report_path: str = typer.Option(
        "data/reports/backtest_segment_report.md",
        help="Output markdown report path.",
    ),
) -> None:
    root = Path(__file__).resolve().parents[1]
    runner = root / "scripts" / "run_backtest.py"
    python = sys.executable

    for family in FAMILIES:
        logger.info("running_family_backtest", family=family)
        subprocess.run(
            [python, str(runner), "--model-family", family],
            check=True,
            cwd=str(root),
        )

    settings = get_settings()
    conn = get_connection(settings)
    try:
        rows = execute_query(
            conn,
            """
            SELECT
              segment_key,
              sample_size,
              survival_auc,
              calibration_ece,
              release_gate_open,
              last_backtest_date,
              notes
            FROM segment_model_evidence
            WHERE segment_key = ANY(%s)
            ORDER BY segment_key
            """,
            (FAMILIES,),
        )
    finally:
        conn.close()

    out = Path(report_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# StartupLens Segment Backtest Report",
        "",
        "| Segment | Sample Size | AUC | ECE | Gate Open | Last Backtest | Notes |",
        "| --- | ---: | ---: | ---: | :---: | --- | --- |",
    ]
    for row in rows:
        row_line = (
            "| {segment_key} | {sample_size} | {survival_auc} | {calibration_ece} | "
            "{release_gate_open} | {last_backtest_date} | {notes} |"
        )
        lines.append(
            row_line.format(
                segment_key=row.get("segment_key"),
                sample_size=row.get("sample_size"),
                survival_auc=row.get("survival_auc"),
                calibration_ece=row.get("calibration_ece"),
                release_gate_open="yes" if row.get("release_gate_open") else "no",
                last_backtest_date=row.get("last_backtest_date"),
                notes=(row.get("notes") or "").replace("|", "/"),
            ),
        )

    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("all_family_backtests_complete", report=str(out))


if __name__ == "__main__":
    app()
