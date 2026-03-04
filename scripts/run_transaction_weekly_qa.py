#!/usr/bin/env python3
"""Weekly QA sampling and field-level reliability metrics for transaction truth."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

import structlog
import typer

from startuplens.config import get_settings
from startuplens.db import execute_query, get_connection

logger = structlog.get_logger(__name__)
app = typer.Typer()


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _as_json(raw: Any) -> Any:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"value": raw}
    return {"value": raw}


def _norm_value(raw: Any) -> Any:
    parsed = _as_json(raw)
    if isinstance(parsed, dict) and "value" in parsed:
        return parsed["value"]
    return parsed


@app.command("sample")
def sample(
    sample_size: int = typer.Option(80, help="Number of field-level QA rows to sample."),
    lookback_days: int = typer.Option(30, help="Sample from rounds updated within this window."),
    min_confidence_score: float = typer.Option(0.6, help="Minimum round confidence to sample."),
) -> None:
    settings = get_settings()
    conn = get_connection(settings)
    try:
        week = _week_start(date.today())
        rows = execute_query(
            conn,
            """
            WITH candidates AS (
              SELECT
                t.transaction_round_id,
                t.field_name,
                t.reconciled_value,
                f.field_value AS source_value
              FROM transaction_round_field_truth t
              JOIN transaction_rounds r ON r.id = t.transaction_round_id
              LEFT JOIN LATERAL (
                SELECT field_value
                FROM transaction_round_field_facts f
                WHERE f.transaction_round_id = t.transaction_round_id
                  AND f.field_name = t.field_name
                ORDER BY
                  CASE f.source_tier WHEN 'A' THEN 0 WHEN 'B' THEN 1 ELSE 2 END,
                  f.as_of_timestamp DESC NULLS LAST,
                  f.created_at DESC
                LIMIT 1
              ) f ON true
              WHERE r.updated_at >= (CURRENT_DATE - (%s || ' days')::interval)
                AND r.confidence_score >= %s
                AND NOT EXISTS (
                  SELECT 1
                  FROM transaction_round_qa_audits qa
                  WHERE qa.audit_week = %s
                    AND qa.transaction_round_id = t.transaction_round_id
                    AND qa.field_name = t.field_name
                )
            )
            SELECT *
            FROM candidates
            ORDER BY random()
            LIMIT %s
            """,
            (lookback_days, min_confidence_score, week, sample_size),
        )

        inserted = 0
        for row in rows:
            execute_query(
                conn,
                """
                INSERT INTO transaction_round_qa_audits (
                  audit_week,
                  transaction_round_id,
                  field_name,
                  truth_value,
                  source_value,
                  is_match,
                  reviewer,
                  notes
                )
                VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, NULL, NULL, 'pending_manual_review')
                """,
                (
                    week,
                    row["transaction_round_id"],
                    row["field_name"],
                    json.dumps(_as_json(row.get("reconciled_value"))),
                    json.dumps(_as_json(row.get("source_value"))),
                ),
            )
            inserted += 1

        conn.commit()
        logger.info(
            "transaction_weekly_qa_sampled",
            audit_week=str(week),
            requested=sample_size,
            inserted=inserted,
        )
    finally:
        conn.close()


@app.command("metrics")
def metrics(
    lookback_weeks: int = typer.Option(12, help="Number of historical weeks to include."),
    min_match_rate: float = typer.Option(0.85, help="Alert threshold for match rate by field."),
) -> None:
    settings = get_settings()
    conn = get_connection(settings)
    try:
        cutoff = _week_start(date.today()) - timedelta(weeks=lookback_weeks)
        rows = execute_query(
            conn,
            """
            SELECT field_name, is_match, truth_value, source_value, audit_week
            FROM transaction_round_qa_audits
            WHERE audit_week >= %s
              AND is_match IS NOT NULL
            ORDER BY audit_week DESC
            """,
            (cutoff,),
        )

        if not rows:
            logger.warning("transaction_weekly_qa_no_labeled_rows", cutoff=str(cutoff))
            return

        by_field: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            by_field[row["field_name"]].append(row)

        alerts: list[dict[str, Any]] = []
        summary: dict[str, dict[str, float | int]] = {}

        for field_name, bucket in by_field.items():
            tp = fp = fn = tn = 0
            for row in bucket:
                pred = _norm_value(row["truth_value"])
                obs = _norm_value(row["source_value"])
                equal = pred == obs
                label_match = bool(row["is_match"])
                if equal and label_match:
                    tp += 1
                elif not equal and not label_match:
                    tn += 1
                elif equal and not label_match:
                    fp += 1
                else:
                    fn += 1

            precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
            recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0
            match_rate = sum(1 for r in bucket if bool(r["is_match"])) / len(bucket)

            summary[field_name] = {
                "sample_size": len(bucket),
                "match_rate": round(match_rate, 4),
                "precision": round(precision, 4),
                "recall": round(recall, 4),
            }

            if match_rate < min_match_rate:
                alerts.append(
                    {
                        "field": field_name,
                        "match_rate": round(match_rate, 4),
                        "threshold": min_match_rate,
                        "sample_size": len(bucket),
                    },
                )

        logger.info(
            "transaction_weekly_qa_metrics",
            lookback_weeks=lookback_weeks,
            fields=len(summary),
            summary=summary,
            alerts=alerts,
        )

        if alerts:
            raise typer.Exit(code=2)
    finally:
        conn.close()


if __name__ == "__main__":
    app()
