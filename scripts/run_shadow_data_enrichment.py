#!/usr/bin/env python3
"""Backfill missing pilot data for the active shadow cycle."""

from __future__ import annotations

import asyncio
import json
import math
import re
from collections.abc import Sequence
from typing import Any

import anthropic
import structlog
import typer

from startuplens.config import get_settings
from startuplens.db import execute_query, get_connection
from startuplens.pipelines.sec_edgar_text import generate_profiles_from_db
from startuplens.scoring.claude_text_scorer import score_batch

logger = structlog.get_logger(__name__)
app = typer.Typer()

_SECTORS = [
    "agriculture",
    "banking and financial services",
    "biotechnology",
    "business services",
    "commercial",
    "computers",
    "construction",
    "energy",
    "health care",
    "insurance",
    "investing",
    "manufacturing",
    "other",
    "other banking and financial services",
    "other energy",
    "other health care",
    "other real estate",
    "other technology",
    "pharmaceuticals",
    "pooled investment fund",
    "real estate",
    "restaurants",
    "retailing",
    "technology",
    "telecommunications",
    "travel",
]
_SECTOR_SET = {s.lower() for s in _SECTORS}
_SECTOR_PROMPT = """\
Classify each company into exactly one sector based only on company name.

Allowed sectors (must match exactly):
{sectors}

Rules:
1. Return ONLY a JSON array of strings in input order.
2. Use "other" if uncertain.
3. No markdown fences, no explanation.

Companies:
{companies}
"""


def _load_cycle(conn, cycle_name: str | None) -> dict[str, Any]:
    if cycle_name:
        rows = execute_query(
            conn,
            """
            SELECT id::text AS id, cycle_name
            FROM shadow_cycles
            WHERE cycle_name = %s
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (cycle_name,),
        )
    else:
        rows = execute_query(
            conn,
            """
            SELECT id::text AS id, cycle_name
            FROM shadow_cycles
            WHERE status = 'active'
            ORDER BY started_at DESC
            LIMIT 1
            """,
        )
    if not rows:
        raise typer.BadParameter("No matching shadow cycle found.")
    return rows[0]


def _load_cycle_companies(conn, cycle_id: str) -> list[dict[str, Any]]:
    return execute_query(
        conn,
        """
        WITH base_items AS (
          SELECT sci.id, sci.entity_id, sci.company_name
          FROM shadow_cycle_items sci
          WHERE sci.cycle_id = %s::uuid
        ),
        bridge AS (
          SELECT
            bi.*,
            (
              SELECT split_part(c.source_id, '_q', 1)
              FROM companies c
              WHERE (
                (bi.entity_id IS NOT NULL AND c.entity_id = bi.entity_id)
                OR lower(c.name) = lower(bi.company_name)
              )
                AND c.source_id IS NOT NULL
              ORDER BY
                CASE c.source
                  WHEN 'sec_dera_cf' THEN 0
                  WHEN 'sec_form_d' THEN 1
                  WHEN 'sec_edgar' THEN 2
                  ELSE 3
                END,
                c.created_at DESC
              LIMIT 1
            ) AS bridge_cik
          FROM base_items bi
        )
        SELECT
          b.id::text AS shadow_cycle_item_id,
          b.company_name,
          c.id::text AS company_id,
          c.source,
          c.source_id
        FROM bridge b
        LEFT JOIN LATERAL (
          SELECT c.*
          FROM companies c
          LEFT JOIN LATERAL (
            SELECT
              co.amount_raised,
              co.had_revenue,
              co.revenue_at_raise
            FROM crowdfunding_outcomes co
            WHERE co.company_id = c.id
            ORDER BY co.campaign_date DESC NULLS LAST
            LIMIT 1
          ) co_sig ON true
          LEFT JOIN LATERAL (
            SELECT fr.amount_raised
            FROM funding_rounds fr
            WHERE fr.company_id = c.id
            ORDER BY fr.round_date DESC NULLS LAST
            LIMIT 1
          ) fr_sig ON true
          LEFT JOIN LATERAL (
            SELECT 1 AS has_text
            FROM sec_form_c_texts sft
            WHERE sft.company_id = c.id
            LIMIT 1
          ) tx_sig ON true
          WHERE (
            (b.entity_id IS NOT NULL AND c.entity_id = b.entity_id)
            OR lower(c.name) = lower(b.company_name)
            OR (
              b.bridge_cik IS NOT NULL
              AND c.source_id IS NOT NULL
              AND split_part(c.source_id, '_q', 1) = b.bridge_cik
            )
          )
          ORDER BY
            CASE
              WHEN COALESCE(co_sig.amount_raised, fr_sig.amount_raised) IS NOT NULL THEN 0
              ELSE 1
            END,
            CASE
              WHEN co_sig.had_revenue IS NOT NULL OR co_sig.revenue_at_raise IS NOT NULL THEN 0
              ELSE 1
            END,
            CASE WHEN tx_sig.has_text = 1 THEN 0 ELSE 1 END,
            CASE c.source
              WHEN 'sec_dera_cf' THEN 0
              WHEN 'sec_form_d' THEN 1
              WHEN 'sec_edgar' THEN 2
              ELSE 3
            END,
            c.created_at DESC
          LIMIT 1
        ) c ON true
        ORDER BY b.company_name
        """,
        (cycle_id,),
    )


def _coverage(conn, cycle_id: str) -> dict[str, int]:
    row = execute_query(
        conn,
        """
        WITH base_items AS (
          SELECT sci.id, sci.entity_id, sci.company_name, sci.created_at::date AS created_date
          FROM shadow_cycle_items sci
          WHERE sci.cycle_id = %s::uuid
        ),
        bridge AS (
          SELECT
            bi.*,
            (
              SELECT split_part(c.source_id, '_q', 1)
              FROM companies c
              WHERE (
                (bi.entity_id IS NOT NULL AND c.entity_id = bi.entity_id)
                OR lower(c.name) = lower(bi.company_name)
              )
                AND c.source_id IS NOT NULL
              ORDER BY
                CASE c.source
                  WHEN 'sec_dera_cf' THEN 0
                  WHEN 'sec_form_d' THEN 1
                  WHEN 'sec_edgar' THEN 2
                  ELSE 3
                END,
                c.created_at DESC
              LIMIT 1
            ) AS bridge_cik
          FROM base_items bi
        ),
        enrichment_company AS (
          SELECT
            b.*,
            (
              SELECT c.id
              FROM companies c
              LEFT JOIN LATERAL (
                SELECT
                  co.amount_raised,
                  co.had_revenue,
                  co.revenue_at_raise
                FROM crowdfunding_outcomes co
                WHERE co.company_id = c.id
                ORDER BY co.campaign_date DESC NULLS LAST
                LIMIT 1
              ) co_sig ON true
              LEFT JOIN LATERAL (
                SELECT fr.amount_raised
                FROM funding_rounds fr
                WHERE fr.company_id = c.id
                ORDER BY fr.round_date DESC NULLS LAST
                LIMIT 1
              ) fr_sig ON true
              LEFT JOIN LATERAL (
                SELECT 1 AS has_text
                FROM sec_form_c_texts sft
                WHERE sft.company_id = c.id
                LIMIT 1
              ) tx_sig ON true
              WHERE (
                (b.entity_id IS NOT NULL AND c.entity_id = b.entity_id)
                OR lower(c.name) = lower(b.company_name)
                OR (
                  b.bridge_cik IS NOT NULL
                  AND c.source_id IS NOT NULL
                  AND split_part(c.source_id, '_q', 1) = b.bridge_cik
                )
              )
              ORDER BY
                CASE
                  WHEN COALESCE(co_sig.amount_raised, fr_sig.amount_raised) IS NOT NULL THEN 0
                  ELSE 1
                END,
                CASE
                  WHEN co_sig.had_revenue IS NOT NULL OR co_sig.revenue_at_raise IS NOT NULL THEN 0
                  ELSE 1
                END,
                CASE WHEN tx_sig.has_text = 1 THEN 0 ELSE 1 END,
                CASE c.source
                  WHEN 'sec_dera_cf' THEN 0
                  WHEN 'sec_form_d' THEN 1
                  WHEN 'sec_edgar' THEN 2
                  ELSE 3
                END,
                c.created_at DESC
              LIMIT 1
            ) AS company_id
          FROM bridge b
        ),
        campaign AS (
          SELECT
            ec.id AS item_id,
            co.campaign_date,
            co.funding_target,
            co.amount_raised,
            co.overfunding_ratio,
            co.pre_money_valuation,
            co.equity_offered,
            co.investor_count AS cf_investor_count,
            co.had_revenue,
            co.revenue_at_raise,
            co.founder_count
          FROM enrichment_company ec
          LEFT JOIN LATERAL (
            SELECT co.*
            FROM crowdfunding_outcomes co
            WHERE co.company_id = ec.company_id
            ORDER BY co.campaign_date DESC NULLS LAST
            LIMIT 1
          ) co ON true
        ),
        financial AS (
          SELECT
            ec.id AS item_id,
            fd.revenue_growth_yoy,
            fd.employee_count,
            fd.burn_rate_monthly,
            fd.total_assets,
            fd.total_debt
          FROM enrichment_company ec
          LEFT JOIN LATERAL (
            SELECT fd.*
            FROM financial_data fd
            WHERE fd.company_id = ec.company_id
            ORDER BY fd.period_end_date DESC NULLS LAST
            LIMIT 1
          ) fd ON true
        ),
        text_profile AS (
          SELECT
            ec.id AS item_id,
            cts.text_quality_score,
            LEFT(sft.narrative_text, 900) AS narrative_excerpt
          FROM enrichment_company ec
          LEFT JOIN LATERAL (
            SELECT sft.id, sft.narrative_text
            FROM sec_form_c_texts sft
            WHERE sft.company_id = ec.company_id
            ORDER BY sft.filing_date DESC NULLS LAST, sft.created_at DESC NULLS LAST
            LIMIT 1
          ) sft ON true
          LEFT JOIN claude_text_scores cts ON cts.form_c_text_id = sft.id
        ),
        comp AS (
          SELECT ec.id AS item_id, c.sector
          FROM enrichment_company ec
          LEFT JOIN companies c ON c.id = ec.company_id
        )
        SELECT
          COUNT(*)::int AS item_count,
          COUNT(*) FILTER (WHERE campaign.campaign_date IS NOT NULL)::int AS campaign_date,
          COUNT(*) FILTER (WHERE campaign.funding_target IS NOT NULL)::int AS funding_target,
          COUNT(*) FILTER (WHERE campaign.amount_raised IS NOT NULL)::int AS amount_raised,
          COUNT(*) FILTER (WHERE campaign.overfunding_ratio IS NOT NULL)::int AS overfunding_ratio,
          COUNT(*) FILTER (
            WHERE campaign.pre_money_valuation IS NOT NULL
          )::int AS pre_money_valuation,
          COUNT(*) FILTER (WHERE campaign.equity_offered IS NOT NULL)::int AS equity_offered,
          COUNT(*) FILTER (WHERE campaign.cf_investor_count IS NOT NULL)::int AS cf_investor_count,
          COUNT(*) FILTER (WHERE campaign.had_revenue IS NOT NULL)::int AS had_revenue,
          COUNT(*) FILTER (WHERE campaign.revenue_at_raise IS NOT NULL)::int AS revenue_at_raise,
          COUNT(*) FILTER (WHERE campaign.founder_count IS NOT NULL)::int AS founder_count,
          COUNT(*) FILTER (
            WHERE financial.revenue_growth_yoy IS NOT NULL
          )::int AS revenue_growth_yoy,
          COUNT(*) FILTER (WHERE financial.employee_count IS NOT NULL)::int AS employee_count,
          COUNT(*) FILTER (WHERE financial.burn_rate_monthly IS NOT NULL)::int AS burn_rate_monthly,
          COUNT(*) FILTER (WHERE financial.total_assets IS NOT NULL)::int AS total_assets,
          COUNT(*) FILTER (WHERE financial.total_debt IS NOT NULL)::int AS total_debt,
          COUNT(*) FILTER (
            WHERE text_profile.narrative_excerpt IS NOT NULL
          )::int AS narrative_excerpt,
          COUNT(*) FILTER (
            WHERE text_profile.text_quality_score IS NOT NULL
          )::int AS text_quality_score,
          COUNT(*) FILTER (WHERE comp.sector IS NOT NULL)::int AS sector
        FROM enrichment_company ec
        LEFT JOIN campaign ON campaign.item_id = ec.id
        LEFT JOIN financial ON financial.item_id = ec.id
        LEFT JOIN text_profile ON text_profile.item_id = ec.id
        LEFT JOIN comp ON comp.item_id = ec.id
        """,
        (cycle_id,),
    )[0]
    return {k: int(v) for k, v in row.items()}


def _run_deterministic_backfills(conn, company_ids: Sequence[str]) -> dict[str, int]:
    if not company_ids:
        return {
            "overfunding_backfilled": 0,
            "company_age_backfilled": 0,
            "sector_backfilled": 0,
            "burn_backfilled": 0,
        }
    company_id_list = list(company_ids)

    overfunding_rows = execute_query(
        conn,
        """
        WITH updated AS (
          UPDATE crowdfunding_outcomes co
          SET overfunding_ratio = ROUND((co.amount_raised / co.funding_target)::numeric, 4)
          WHERE co.company_id = ANY(%s::uuid[])
            AND co.overfunding_ratio IS NULL
            AND co.amount_raised IS NOT NULL
            AND co.funding_target IS NOT NULL
            AND co.funding_target > 0
          RETURNING 1
        )
        SELECT COUNT(*)::int AS updated_count FROM updated
        """,
        (company_id_list,),
    )

    age_rows = execute_query(
        conn,
        """
        WITH updated AS (
          UPDATE crowdfunding_outcomes co
          SET company_age_at_raise_months = (
            (EXTRACT(YEAR FROM age(co.campaign_date, c.founding_date))::int * 12)
            + EXTRACT(MONTH FROM age(co.campaign_date, c.founding_date))::int
          )
          FROM companies c
          WHERE co.company_id = c.id
            AND co.company_id = ANY(%s::uuid[])
            AND co.company_age_at_raise_months IS NULL
            AND co.campaign_date IS NOT NULL
            AND c.founding_date IS NOT NULL
            AND co.campaign_date >= c.founding_date
          RETURNING 1
        )
        SELECT COUNT(*)::int AS updated_count FROM updated
        """,
        (company_id_list,),
    )

    sector_rows = execute_query(
        conn,
        """
        WITH updated AS (
          UPDATE crowdfunding_outcomes co
          SET sector = c.sector
          FROM companies c
          WHERE co.company_id = c.id
            AND co.company_id = ANY(%s::uuid[])
            AND co.sector IS NULL
            AND c.sector IS NOT NULL
          RETURNING 1
        )
        SELECT COUNT(*)::int AS updated_count FROM updated
        """,
        (company_id_list,),
    )

    burn_rows = execute_query(
        conn,
        """
        WITH updated AS (
          UPDATE financial_data fd
          SET burn_rate_monthly = ROUND((ABS(fd.net_income) / 12.0)::numeric, 2)
          WHERE fd.company_id = ANY(%s::uuid[])
            AND fd.burn_rate_monthly IS NULL
            AND fd.net_income IS NOT NULL
            AND fd.net_income < 0
          RETURNING 1
        )
        SELECT COUNT(*)::int AS updated_count FROM updated
        """,
        (company_id_list,),
    )

    conn.commit()
    return {
        "overfunding_backfilled": int(overfunding_rows[0]["updated_count"]),
        "company_age_backfilled": int(age_rows[0]["updated_count"]),
        "sector_backfilled": int(sector_rows[0]["updated_count"]),
        "burn_backfilled": int(burn_rows[0]["updated_count"]),
    }


def _chunks(rows: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [rows[i : i + size] for i in range(0, len(rows), size)]


def _parse_json_array(text: str) -> list[str]:
    cleaned = text.strip()
    fence_match = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1).strip()
    array_match = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if array_match:
        cleaned = array_match.group(0)
    data = json.loads(cleaned)
    if not isinstance(data, list):
        raise ValueError("sector response is not a list")
    return [str(v).strip().lower() for v in data]


async def _classify_sector_batch(
    client: anthropic.AsyncAnthropic,
    batch: list[dict[str, Any]],
    semaphore: asyncio.Semaphore,
) -> list[tuple[str, str]]:
    async with semaphore:
        names = [str(r["name"]) for r in batch]
        numbered = "\n".join(f"{i + 1}. {name}" for i, name in enumerate(names))
        prompt = _SECTOR_PROMPT.format(
            sectors=", ".join(_SECTORS),
            companies=numbered,
        )
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text if response.content else "[]"
        parsed = _parse_json_array(raw)
        results: list[tuple[str, str]] = []
        for idx, row in enumerate(batch):
            sector = parsed[idx] if idx < len(parsed) else "other"
            if sector not in _SECTOR_SET:
                sector = "other"
            results.append((str(row["id"]), sector))
        return results


def _classify_null_sectors(
    conn,
    settings,
    company_ids: Sequence[str],
    *,
    max_concurrent: int = 4,
) -> int:
    if not company_ids or not settings.anthropic_api_key:
        return 0
    rows = execute_query(
        conn,
        """
        SELECT id::text AS id, name
        FROM companies
        WHERE id = ANY(%s::uuid[])
          AND sector IS NULL
        ORDER BY name
        """,
        (list(company_ids),),
    )
    if not rows:
        return 0

    async def _run() -> list[tuple[str, str]]:
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        semaphore = asyncio.Semaphore(max_concurrent)
        batches = _chunks(rows, 25)
        tasks = [_classify_sector_batch(client, batch, semaphore) for batch in batches]
        output: list[tuple[str, str]] = []
        try:
            for batch_result in await asyncio.gather(*tasks):
                output.extend(batch_result)
        finally:
            await client.close()
        return output

    classified = asyncio.run(_run())
    if not classified:
        return 0

    with conn.cursor() as cur:
        cur.executemany(
            """
            UPDATE companies
            SET sector = %s, sic_code = %s
            WHERE id = %s::uuid
            """,
            [(sector, sector, company_id) for company_id, sector in classified],
        )
        updated = cur.rowcount
    conn.commit()
    return int(updated)


def _log_coverage_delta(before: dict[str, int], after: dict[str, int]) -> None:
    item_count = max(1, int(before.get("item_count", 1)))
    keys = [k for k in before.keys() if k != "item_count"]
    for key in keys:
        b = int(before.get(key, 0))
        a = int(after.get(key, 0))
        if a != b:
            logger.info(
                "coverage_delta",
                field=key,
                before=b,
                after=a,
                delta=a - b,
                before_pct=round((b / item_count) * 100, 2),
                after_pct=round((a / item_count) * 100, 2),
            )


@app.command()
def main(
    cycle_name: str | None = typer.Option(
        None,
        help="Optional cycle_name; defaults to latest active cycle.",
    ),
    max_text_score: int = typer.Option(
        200,
        help="Max number of target company texts to score this run.",
    ),
) -> None:
    settings = get_settings()
    conn = get_connection(settings)
    try:
        cycle = _load_cycle(conn, cycle_name)
        cycle_id = str(cycle["id"])
        company_rows = _load_cycle_companies(conn, cycle_id)
        company_ids = [str(r["company_id"]) for r in company_rows if r.get("company_id")]
        unique_company_ids = sorted(set(company_ids))
        if not unique_company_ids:
            raise typer.BadParameter("No mapped companies found for selected cycle.")

        before = _coverage(conn, cycle_id)

        deterministic = _run_deterministic_backfills(conn, unique_company_ids)
        sectors_updated = _classify_null_sectors(conn, settings, unique_company_ids)
        profiles_generated = generate_profiles_from_db(
            conn,
            include_unknown_outcomes=True,
            company_ids=unique_company_ids,
        )
        scored = score_batch(
            conn,
            settings,
            limit=max_text_score if max_text_score > 0 else None,
            company_ids=unique_company_ids,
        )
        conn.commit()
        after = _coverage(conn, cycle_id)

        logger.info(
            "shadow_data_enrichment_complete",
            cycle_name=cycle["cycle_name"],
            cycle_id=cycle_id,
            mapped_companies=len(unique_company_ids),
            overfunding_backfilled=deterministic["overfunding_backfilled"],
            company_age_backfilled=deterministic["company_age_backfilled"],
            sector_backfilled_in_outcomes=deterministic["sector_backfilled"],
            burn_backfilled=deterministic["burn_backfilled"],
            sectors_classified=sectors_updated,
            profiles_generated=profiles_generated,
            text_scored=scored,
            coverage_before=before,
            coverage_after=after,
        )
        _log_coverage_delta(before, after)

        typer.echo(
            json.dumps(
                {
                    "cycle_name": cycle["cycle_name"],
                    "cycle_id": cycle_id,
                    "mapped_companies": len(unique_company_ids),
                    "deterministic_backfills": deterministic,
                    "sectors_classified": sectors_updated,
                    "profiles_generated": profiles_generated,
                    "text_scored": scored,
                    "coverage_before": before,
                    "coverage_after": after,
                },
                indent=2,
                default=lambda v: None if isinstance(v, float) and math.isnan(v) else v,
            )
        )
    finally:
        conn.close()


if __name__ == "__main__":
    app()
