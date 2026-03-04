#!/usr/bin/env python3
"""Run operational guardrail checks and enqueue alert events when thresholds break."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import structlog
import typer

from startuplens.config import get_settings
from startuplens.db import execute_query, get_connection

logger = structlog.get_logger(__name__)
app = typer.Typer()


def _json_default(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _quarter_start(d: date) -> date:
    month = ((d.month - 1) // 3) * 3 + 1
    return date(d.year, month, 1)


def _enqueue(conn, alert_type: str, priority: str, dedupe_key: str, payload: dict) -> None:
    exists = execute_query(
        conn,
        """
        SELECT 1
        FROM alert_events
        WHERE dedupe_key = %s
          AND status IN ('queued', 'sent')
        LIMIT 1
        """,
        (dedupe_key,),
    )
    if exists:
        return
    execute_query(
        conn,
        """
        INSERT INTO alert_events (
          alert_type,
          priority,
          dedupe_key,
          payload,
          queued_at,
          status
        )
        VALUES (%s, %s, %s, %s::jsonb, now(), 'queued')
        """,
        (alert_type, priority, dedupe_key, json.dumps(payload, default=_json_default)),
    )


@app.command()
def main(
    enqueue_alerts: bool = typer.Option(True, help="Write alert_events rows when checks fail."),
    fail_on_alerts: bool = typer.Option(
        True,
        help="Exit non-zero when any alert condition is hit.",
    ),
    output_file: str = typer.Option("", help="Optional JSON output path for CI artifacts."),
) -> None:
    settings = get_settings()
    conn = get_connection(settings)
    alerts: list[dict] = []

    try:
        current_quarter = _quarter_start(date.today())

        evidence = execute_query(
            conn,
            """
            SELECT report_quarter, generated_at::text, release_readiness
            FROM quarterly_evidence_reports
            ORDER BY report_quarter DESC
            LIMIT 1
            """,
        )
        if not evidence:
            alerts.append(
                {
                    "type": "evidence_missing",
                    "priority": "critical",
                    "dedupe": "evidence_missing",
                    "payload": {"reason": "No quarterly evidence report found"},
                },
            )
        else:
            e = evidence[0]
            if not e.get("release_readiness"):
                alerts.append(
                    {
                        "type": "evidence_gate_closed",
                        "priority": "critical",
                        "dedupe": f"evidence_gate_closed:{e['report_quarter']}",
                        "payload": {
                            "report_quarter": str(e["report_quarter"]),
                            "generated_at": e.get("generated_at"),
                        },
                    },
                )
            if e.get("report_quarter") and e["report_quarter"] < current_quarter:
                alerts.append(
                    {
                        "type": "evidence_stale",
                        "priority": "high",
                        "dedupe": f"evidence_stale:{e['report_quarter']}",
                        "payload": {
                            "report_quarter": str(e["report_quarter"]),
                            "current_quarter": str(current_quarter),
                        },
                    },
                )

        drift_rows = execute_query(
            conn,
            """
            SELECT segment_key, sample_size, survival_auc, calibration_ece, release_gate_open
            FROM segment_model_evidence
            WHERE (
              calibration_ece > 0.10
              OR survival_auc < 0.65
              OR release_gate_open = false
            )
            """,
        )
        for row in drift_rows:
            alerts.append(
                {
                    "type": "segment_drift",
                    "priority": "high",
                    "dedupe": f"segment_drift:{row['segment_key']}",
                    "payload": {
                        "segment": row["segment_key"],
                        "sample_size": row.get("sample_size"),
                        "survival_auc": row.get("survival_auc"),
                        "calibration_ece": row.get("calibration_ece"),
                        "release_gate_open": row.get("release_gate_open"),
                    },
                },
            )

        backtest_rows = execute_query(
            conn,
            """
            WITH latest AS (
              SELECT DISTINCT ON (segment_key)
                segment_key,
                window_label,
                test_end,
                quality_vs_random,
                failure_vs_random
              FROM backtest_window_results
              ORDER BY segment_key, test_end DESC, window_label DESC
            )
            SELECT *
            FROM latest
            WHERE quality_vs_random <= 1.3
               OR failure_vs_random >= 0.7
            """,
        )
        for row in backtest_rows:
            alerts.append(
                {
                    "type": "backtest_regression",
                    "priority": "high",
                    "dedupe": f"backtest_regression:{row['segment_key']}:{row['window_label']}",
                    "payload": {
                        "segment": row["segment_key"],
                        "window": row.get("window_label"),
                        "quality_vs_random": row.get("quality_vs_random"),
                        "failure_vs_random": row.get("failure_vs_random"),
                        "test_end": str(row.get("test_end")) if row.get("test_end") else None,
                    },
                },
            )

        qa_rows = execute_query(
            conn,
            """
            WITH recent AS (
              SELECT field_name, is_match
              FROM transaction_round_qa_audits
              WHERE audit_week >= CURRENT_DATE - INTERVAL '12 weeks'
                AND is_match IS NOT NULL
            )
            SELECT
              field_name,
              COUNT(*) AS sample_size,
              AVG(CASE WHEN is_match THEN 1.0 ELSE 0.0 END) AS match_rate
            FROM recent
            GROUP BY field_name
            HAVING COUNT(*) >= 10
               AND AVG(CASE WHEN is_match THEN 1.0 ELSE 0.0 END) < 0.85
            """,
        )
        for row in qa_rows:
            alerts.append(
                {
                    "type": "transaction_qa_drift",
                    "priority": "high",
                    "dedupe": f"transaction_qa_drift:{row['field_name']}",
                    "payload": {
                        "field_name": row["field_name"],
                        "sample_size": int(row.get("sample_size") or 0),
                        "match_rate": float(row.get("match_rate") or 0),
                    },
                },
            )

        if enqueue_alerts and alerts:
            for alert in alerts:
                _enqueue(
                    conn,
                    alert["type"],
                    alert["priority"],
                    alert["dedupe"],
                    alert["payload"],
                )

        conn.commit()
    finally:
        conn.close()

    summary = {"alert_count": len(alerts), "alerts": alerts}
    if output_file:
        p = Path(output_file)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(summary, indent=2, default=_json_default), encoding="utf-8")

    if alerts:
        logger.error("ops_alert_checks_failed", alert_count=len(alerts))
        if fail_on_alerts:
            raise typer.Exit(code=1)
    else:
        logger.info("ops_alert_checks_passed")


if __name__ == "__main__":
    app()
