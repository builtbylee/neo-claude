#!/usr/bin/env python3
"""One-command release pipeline: migrate, deploy, and smoke-check."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import structlog
import typer

logger = structlog.get_logger(__name__)
app = typer.Typer()


def _run(cmd: list[str], cwd: Path) -> None:
    logger.info("run_cmd", cmd=" ".join(cmd), cwd=str(cwd))
    subprocess.run(cmd, check=True, cwd=str(cwd))


@app.command()
def main(
    base_url: str = typer.Option(
        "https://startuplens.lee-sam78.workers.dev",
        help="Target URL for smoke checks.",
    ),
    user_email: str = typer.Option(
        "owner@example.com",
        help="User email for smoke checks.",
    ),
) -> None:
    root = Path(__file__).resolve().parents[1]
    web = root / "web"
    python = sys.executable

    _run([python, "scripts/run_db_migrations.py"], root)
    _run([python, "scripts/run_all_family_backtests.py"], root)
    _run([python, "scripts/run_benchmark_report.py"], root)
    _run(["npm", "run", "build:cf"], web)
    _run(["npm", "run", "deploy"], web)
    _run(
        [
            python,
            "scripts/run_smoke_checks.py",
            "--base-url",
            base_url,
            "--user-email",
            user_email,
        ],
        root,
    )
    _run(
        [
            python,
            "scripts/run_workflow_smoke.py",
            "--base-url",
            base_url,
            "--user-email",
            user_email,
        ],
        root,
    )
    logger.info("release_pipeline_complete")


if __name__ == "__main__":
    app()
