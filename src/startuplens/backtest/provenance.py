"""Backtest run provenance logging.

Every backtest run is recorded with full reproducibility metadata:
model family, version, data snapshot, feature set, metrics, and
pass/fail verdicts.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from startuplens.db import execute_query


def log_backtest_run(
    conn: Any,
    *,
    model_family: str,
    model_version_id: int | None = None,
    data_snapshot_date: date,
    train_window: str,
    test_window: str,
    features_active: list[str],
    alt_data_signals: list[str] | None = None,
    metrics: dict[str, Any],
    baselines: dict[str, Any],
    pass_fail: dict[str, Any],
    all_passed: bool,
    notes: str | None = None,
) -> int:
    """Record a backtest run with full provenance metadata.

    Returns
    -------
    int
        The serial ``id`` of the newly created backtest_runs row.
    """
    rows = execute_query(
        conn,
        """
        INSERT INTO backtest_runs (
            model_family, model_version_id, data_snapshot_date,
            train_window, test_window, features_active,
            alt_data_signals_included, metrics, baselines,
            pass_fail, all_passed, notes
        ) VALUES (
            %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb,
            %s::jsonb, %s::jsonb, %s, %s
        )
        RETURNING id
        """,
        (
            model_family,
            model_version_id,
            data_snapshot_date,
            train_window,
            test_window,
            json.dumps(features_active),
            json.dumps(alt_data_signals or []),
            json.dumps(metrics),
            json.dumps(baselines),
            json.dumps(pass_fail),
            all_passed,
            notes,
        ),
    )
    return rows[0]["id"]


def get_backtest_run(conn: Any, run_id: int) -> dict | None:
    """Retrieve a single backtest run by ID."""
    rows = execute_query(
        conn,
        "SELECT * FROM backtest_runs WHERE id = %s",
        (run_id,),
    )
    return rows[0] if rows else None


def get_latest_runs(
    conn: Any,
    model_family: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """Retrieve the most recent backtest runs, optionally filtered by model family."""
    if model_family:
        return execute_query(
            conn,
            """
            SELECT * FROM backtest_runs
            WHERE model_family = %s
            ORDER BY run_date DESC LIMIT %s
            """,
            (model_family, limit),
        )
    return execute_query(
        conn,
        "SELECT * FROM backtest_runs ORDER BY run_date DESC LIMIT %s",
        (limit,),
    )


def get_passing_runs(
    conn: Any,
    model_family: str | None = None,
) -> list[dict]:
    """Retrieve all runs where all must-pass metrics passed."""
    if model_family:
        return execute_query(
            conn,
            """
            SELECT * FROM backtest_runs
            WHERE all_passed = true AND model_family = %s
            ORDER BY run_date DESC
            """,
            (model_family,),
        )
    return execute_query(
        conn,
        "SELECT * FROM backtest_runs WHERE all_passed = true ORDER BY run_date DESC",
    )


def compare_runs(conn: Any, run_id_a: int, run_id_b: int) -> dict:
    """Compare metrics between two backtest runs.

    Returns a dict with keys for each metric showing both values and the delta.
    """
    run_a = get_backtest_run(conn, run_id_a)
    run_b = get_backtest_run(conn, run_id_b)

    if not run_a or not run_b:
        missing = []
        if not run_a:
            missing.append(run_id_a)
        if not run_b:
            missing.append(run_id_b)
        return {"error": f"Run(s) not found: {missing}"}

    metrics_a = run_a["metrics"] if isinstance(run_a["metrics"], dict) else {}
    metrics_b = run_b["metrics"] if isinstance(run_b["metrics"], dict) else {}

    all_keys = sorted(set(metrics_a) | set(metrics_b))
    comparison = {}
    for key in all_keys:
        val_a = metrics_a.get(key)
        val_b = metrics_b.get(key)
        delta = None
        if isinstance(val_a, (int, float)) and isinstance(val_b, (int, float)):
            delta = val_b - val_a
        comparison[key] = {"run_a": val_a, "run_b": val_b, "delta": delta}

    return {
        "run_a_id": run_id_a,
        "run_b_id": run_id_b,
        "run_a_passed": run_a["all_passed"],
        "run_b_passed": run_b["all_passed"],
        "metrics": comparison,
    }
