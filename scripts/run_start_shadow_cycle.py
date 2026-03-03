#!/usr/bin/env python3
"""Start a 25-deal shadow evaluation cycle for live tracking."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import structlog
import typer

from startuplens.config import get_settings
from startuplens.db import execute_query, get_connection

logger = structlog.get_logger(__name__)
app = typer.Typer()


def _json_default(value):
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


@app.command()
def main(
    cycle_name: str = typer.Option("", help="Cycle label (defaults to current date)."),
    target_count: int = typer.Option(25, help="Number of deals to include."),
    force_new: bool = typer.Option(False, help="Allow creating another active cycle."),
    note: str = typer.Option("Initial production shadow cycle", help="Cycle note."),
) -> None:
    if target_count <= 0:
        raise typer.BadParameter("target_count must be positive")

    label = cycle_name.strip() or f"shadow-{date.today().isoformat()}"

    settings = get_settings()
    conn = get_connection(settings)
    try:
        active = execute_query(
            conn,
            """
            SELECT id::text, cycle_name, started_at::text
            FROM shadow_cycles
            WHERE status = 'active'
            ORDER BY started_at DESC
            LIMIT 1
            """,
        )
        if active and not force_new:
            logger.error(
                "shadow_cycle_exists",
                cycle_id=active[0]["id"],
                cycle_name=active[0]["cycle_name"],
                started_at=active[0]["started_at"],
            )
            raise typer.Exit(code=2)

        freeze_rows = execute_query(
            conn,
            """
            SELECT id::text
            FROM threshold_freezes
            WHERE active = true
            ORDER BY created_at DESC
            LIMIT 1
            """,
        )
        freeze_id = freeze_rows[0]["id"] if freeze_rows else None

        policy_rows = execute_query(
            conn,
            """
            SELECT max_investments_per_year, max_per_sector_per_year, check_size,
                   check_currency, no_forced_deployment, compliance_hard_blocks
            FROM investor_policy
            ORDER BY updated_at DESC
            LIMIT 1
            """,
        )
        policy = policy_rows[0] if policy_rows else {
            "max_investments_per_year": 2,
            "max_per_sector_per_year": 1,
            "check_size": 10000,
            "check_currency": "GBP",
            "no_forced_deployment": True,
            "compliance_hard_blocks": True,
        }

        candidates = execute_query(
            conn,
            """
            SELECT
              COALESCE(df.entity_id, ce.id) AS entity_id,
              da.company_name,
              COALESCE(da.sector, df.sector, ce.sector) AS sector,
              COALESCE(df.country, ce.country, 'US') AS country,
              'deal_alert'::text AS source,
              da.id::text AS source_ref
            FROM deal_alerts da
            LEFT JOIN deal_funnel df ON df.id = da.deal_funnel_id
            LEFT JOIN canonical_entities ce
              ON lower(ce.primary_name) = lower(da.company_name)
            ORDER BY da.alert_date DESC
            LIMIT %s
            """,
            (target_count * 3,),
        )

        if len(candidates) < target_count:
            fallback = execute_query(
                conn,
                """
                SELECT
                  c.entity_id,
                  c.name AS company_name,
                  co.sector,
                  co.country,
                  'crowdfunding_outcome'::text AS source,
                  co.id::text AS source_ref
                FROM crowdfunding_outcomes co
                JOIN companies c ON c.id = co.company_id
                ORDER BY co.campaign_date DESC
                LIMIT %s
                """,
                (target_count * 3,),
            )
            candidates.extend(fallback)

        deduped: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for row in candidates:
            key = ((row.get("company_name") or "").strip().lower(), row.get("source_ref") or "")
            if not key[0] or key in seen:
                continue
            seen.add(key)
            deduped.append(row)
            if len(deduped) >= target_count:
                break

        if len(deduped) < target_count:
            logger.error(
                "shadow_cycle_insufficient_candidates",
                found=len(deduped),
                required=target_count,
            )
            raise typer.Exit(code=2)

        cycle = execute_query(
            conn,
            """
            INSERT INTO shadow_cycles (
              cycle_name,
              target_count,
              status,
              policy_snapshot,
              threshold_freeze_id,
              started_at,
              notes
            )
            VALUES (%s, %s, 'active', %s::jsonb, %s::uuid, now(), %s)
            RETURNING id::text
            """,
            (label, target_count, json.dumps(policy, default=_json_default), freeze_id, note),
        )
        cycle_id = cycle[0]["id"]

        for item in deduped:
            execute_query(
                conn,
                """
                INSERT INTO shadow_cycle_items (
                  cycle_id,
                  entity_id,
                  company_name,
                  sector,
                  country,
                  source,
                  source_ref,
                  created_at
                )
                VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s, %s, now())
                ON CONFLICT (cycle_id, company_name, source, source_ref) DO NOTHING
                """,
                (
                    cycle_id,
                    item.get("entity_id"),
                    item.get("company_name"),
                    item.get("sector"),
                    item.get("country"),
                    item.get("source"),
                    item.get("source_ref"),
                ),
            )

        conn.commit()
        logger.info(
            "shadow_cycle_started",
            cycle_id=cycle_id,
            cycle_name=label,
            target_count=target_count,
            threshold_freeze_id=freeze_id,
        )
    finally:
        conn.close()


if __name__ == "__main__":
    app()
