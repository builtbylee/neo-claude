#!/usr/bin/env python3
"""Compute valuation MAE/MAPE by quarter + segment + confidence cohort."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime

import structlog
import typer

from startuplens.config import get_settings
from startuplens.db import execute_query, get_connection

logger = structlog.get_logger(__name__)
app = typer.Typer()


def _quarter_start(dt: date) -> date:
    month = ((dt.month - 1) // 3) * 3 + 1
    return date(dt.year, month, 1)


def _to_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


@app.command()
def main(
    lookback_quarters: int = typer.Option(
        12,
        help="How many recent quarters to compute.",
    ),
) -> None:
    settings = get_settings()
    conn = get_connection(settings)
    try:
        rows = execute_query(
            conn,
            """
            SELECT
                segment_key,
                valuation_confidence,
                base_moic,
                realized_moic,
                realized_at,
                created_at,
                valuation_source_summary
            FROM valuation_scenario_audits
            WHERE segment_key IN ('US_Seed', 'US_EarlyGrowth', 'UK_Seed', 'UK_EarlyGrowth')
              AND valuation_confidence IN ('high', 'medium', 'low')
              AND base_moic IS NOT NULL
              AND realized_moic IS NOT NULL
            ORDER BY COALESCE(realized_at, created_at) DESC
            """,
        )

        grouped: dict[tuple[date, str, str], list[dict]] = defaultdict(list)
        totals: dict[tuple[date, str], int] = defaultdict(int)

        for row in rows:
            dt = _to_date(row.get("realized_at")) or _to_date(row.get("created_at"))
            if dt is None:
                continue
            q = _quarter_start(dt)
            segment = row.get("segment_key")
            confidence = row.get("valuation_confidence")
            if segment is None or confidence is None:
                continue
            grouped[(q, segment, confidence)].append(row)
            totals[(q, segment)] += 1

        recent_quarters = sorted({k[0] for k in grouped.keys()}, reverse=True)[:lookback_quarters]
        allowed = set(recent_quarters)

        upserted = 0
        for (q, segment, confidence), bucket in grouped.items():
            if q not in allowed:
                continue
            errors = []
            pct_errors = []
            tier_counts = {"A": 0, "B": 0, "C": 0}

            for row in bucket:
                pred = float(row["base_moic"])
                realized = float(row["realized_moic"])
                errors.append(abs(pred - realized))
                denom = abs(realized) if abs(realized) > 1e-6 else 1.0
                pct_errors.append(abs(pred - realized) / denom)

                summary = row.get("valuation_source_summary")
                if isinstance(summary, str):
                    try:
                        summary = json.loads(summary)
                    except json.JSONDecodeError:
                        summary = None
                tier_mix = (
                    summary.get("pricingTierBreakdown")
                    if isinstance(summary, dict)
                    else None
                )
                if isinstance(tier_mix, dict):
                    for tier in ("A", "B", "C"):
                        value = tier_mix.get(tier)
                        if isinstance(value, (int, float)):
                            tier_counts[tier] += int(value)

            sample_size = len(bucket)
            mae = sum(errors) / sample_size if sample_size > 0 else None
            mape = sum(pct_errors) / sample_size if sample_size > 0 else None
            coverage_ratio = (
                sample_size / totals[(q, segment)]
                if totals[(q, segment)] > 0 else 0
            )

            execute_query(
                conn,
                """
                INSERT INTO valuation_cohort_mae (
                    cohort_quarter,
                    segment_key,
                    valuation_confidence,
                    sample_size,
                    mae,
                    mape,
                    coverage_ratio,
                    source_tier_mix,
                    computed_at,
                    notes
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, now(), %s)
                ON CONFLICT (cohort_quarter, segment_key, valuation_confidence) DO UPDATE
                SET
                    sample_size = EXCLUDED.sample_size,
                    mae = EXCLUDED.mae,
                    mape = EXCLUDED.mape,
                    coverage_ratio = EXCLUDED.coverage_ratio,
                    source_tier_mix = EXCLUDED.source_tier_mix,
                    computed_at = now(),
                    notes = EXCLUDED.notes
                """,
                (
                    q,
                    segment,
                    confidence,
                    sample_size,
                    mae,
                    mape,
                    coverage_ratio,
                    json.dumps(tier_counts),
                    "Computed from valuation_scenario_audits with realized outcomes",
                ),
            )
            upserted += 1

        conn.commit()
        logger.info(
            "valuation_cohort_mae_computed",
            source_rows=len(rows),
            cohorts=upserted,
            quarters=len(allowed),
        )
    finally:
        conn.close()


if __name__ == "__main__":
    app()
