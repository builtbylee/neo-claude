#!/usr/bin/env python3
"""Production readiness gate — verifies all backtest metrics pass before deploy.

Runs the walk-forward backtest, checks all 8 metrics against thresholds,
re-exports the model if metrics pass, and exits with appropriate code.

Exit codes:
    0: All must-pass metrics pass, model exported.
    1: One or more must-pass metrics failed.
    2: Backtest could not run (data or connection error).

Usage:
    python scripts/production_gate.py
    python scripts/production_gate.py --skip-export
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import structlog
import typer

from startuplens.config import get_settings
from startuplens.db import execute_query, get_connection, refresh_matview

logger = structlog.get_logger(__name__)
app = typer.Typer()


def _load_latest_backtest_run(conn) -> dict | None:
    """Load the most recent backtest run from the database."""
    rows = execute_query(
        conn,
        """
        SELECT id, run_date, metrics, pass_fail, all_passed, notes
        FROM backtest_runs
        ORDER BY id DESC
        LIMIT 1
        """,
    )
    if not rows:
        return None
    return rows[0]


@app.command()
def main(
    skip_backtest: bool = typer.Option(
        False, "--skip-backtest",
        help="Skip running the backtest and use the latest stored results",
    ),
    skip_export: bool = typer.Option(
        False, "--skip-export",
        help="Skip model export even if metrics pass",
    ),
    output: str = typer.Option(
        "web/public/model/model.json",
        help="Output path for the exported model JSON",
    ),
) -> None:
    """Run the production readiness gate.

    Checks all 8 backtest metrics against their thresholds and optionally
    re-exports the model if all must-pass metrics are met.
    """
    settings = get_settings()
    conn = get_connection(settings)

    try:
        # Step 1: Refresh materialized view
        logger.info("refreshing_matview")
        refresh_matview(conn)
        logger.info("matview_refreshed")
        pre_backtest_run = _load_latest_backtest_run(conn)
        pre_backtest_run_id = pre_backtest_run["id"] if pre_backtest_run else None

        if not skip_backtest:
            # Step 2: Run the backtest
            logger.info("running_backtest")
            result = subprocess.run(
                [sys.executable, "scripts/run_backtest.py"],
                capture_output=True,
                text=True,
                timeout=600,
            )
            if result.returncode != 0:
                logger.error(
                    "backtest_failed",
                    returncode=result.returncode,
                    stderr=result.stderr[-500:] if result.stderr else "",
                )
                typer.echo("FAIL: Backtest could not complete.", err=True)
                raise SystemExit(2)

        # Step 3: Load the latest backtest results
        run = _load_latest_backtest_run(conn)
        if run is None:
            logger.error("no_backtest_runs_found")
            typer.echo("FAIL: No backtest runs found in database.", err=True)
            raise SystemExit(2)
        if (
            not skip_backtest
            and pre_backtest_run_id is not None
            and run["id"] <= pre_backtest_run_id
        ):
            logger.error(
                "backtest_run_not_fresh",
                previous_run_id=pre_backtest_run_id,
                latest_run_id=run["id"],
            )
            typer.echo(
                "FAIL: Backtest did not produce a new run; refusing to use stale metrics.",
                err=True,
            )
            raise SystemExit(2)

        metrics_raw = run["metrics"]
        if isinstance(metrics_raw, str):
            metrics_raw = json.loads(metrics_raw)

        pass_fail_raw = run["pass_fail"]
        if isinstance(pass_fail_raw, str):
            pass_fail_raw = json.loads(pass_fail_raw)

        all_passed = run["all_passed"]

        # Step 4: Display results
        typer.echo("\n" + "=" * 60)
        typer.echo("  PRODUCTION READINESS GATE")
        typer.echo("=" * 60)
        typer.echo(f"  Backtest Run ID: {run['id']}")
        typer.echo(f"  Run Date: {run['run_date']}")
        typer.echo("-" * 60)

        for metric_name, detail in pass_fail_raw.items():
            value = detail.get("value")
            threshold = detail.get("threshold")
            passed = detail.get("passed", False)
            status = "PASS" if passed else "FAIL"
            value_str = f"{value:.3f}" if value is not None else "N/A"
            threshold_str = f"{threshold}" if threshold is not None else "N/A"
            typer.echo(
                f"  [{status}] {metric_name}: {value_str} "
                f"(threshold: {threshold_str})"
            )

        typer.echo("-" * 60)

        if all_passed:
            typer.echo("  RESULT: ALL MUST-PASS METRICS MET")
        else:
            typer.echo("  RESULT: GATE FAILED — must-pass metrics not met")
            failed = [
                name for name, d in pass_fail_raw.items()
                if not d.get("passed", False)
            ]
            typer.echo(f"  Failed: {', '.join(failed)}")

        typer.echo("=" * 60 + "\n")

        if not all_passed:
            raise SystemExit(1)

        # Step 5: Re-export model if metrics pass
        if not skip_export:
            logger.info("exporting_model", output=output)
            result = subprocess.run(
                [sys.executable, "scripts/export_model.py", "--output", output],
                capture_output=True,
                text=True,
                timeout=300,
            )
            if result.returncode != 0:
                logger.error(
                    "model_export_failed",
                    returncode=result.returncode,
                    stderr=result.stderr[-500:] if result.stderr else "",
                )
                typer.echo("FAIL: Model export failed.", err=True)
                raise SystemExit(2)
            else:
                size = Path(output).stat().st_size / 1024
                typer.echo(f"Model exported to {output} ({size:.1f} KB)")

        typer.echo("Production gate PASSED.")

    finally:
        conn.close()


if __name__ == "__main__":
    app()
