#!/usr/bin/env python3
"""True multi-agent synthetic analyst pilot.

Unlike the single-loop pilot, this orchestrator fans out 3 independent
analyst agents (conservative, balanced, aggressive) as parallel async tasks
with isolated per-agent state.  Results are aggregated into a single pilot
report after all agents complete.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import anthropic
import structlog
import typer

from startuplens.config import get_settings
from startuplens.db import execute_query, get_connection

logger = structlog.get_logger(__name__)
app = typer.Typer()

MODEL = "claude-haiku-4-5-20251001"

RECOMMENDATIONS = {"invest", "deep_diligence", "watch", "pass", "abstain"}
SEED_EVAL_NOTE = "seeded_shadow_model_eval_v2"
DEFAULT_MIN_SUFFICIENCY_SCORE = 50.0
DEFAULT_MIN_CATEGORY_COUNT = 3
DEFAULT_MIN_MODEL_COVERAGE = 0.30
DEFAULT_MIN_CLASS_COVERAGE = 3

AGENT_PROFILES: list[dict[str, str]] = [
    {
        "id": "conservative",
        "name": "Conservative Risk Analyst",
        "prompt": (
            "You are a conservative VC analyst. Capital preservation first. "
            "Heavily penalize weak evidence, overvaluation, and execution risk."
        ),
    },
    {
        "id": "balanced",
        "name": "Balanced Fundamental Analyst",
        "prompt": (
            "You are a balanced VC analyst. Weigh upside and downside evenly. "
            "Prefer evidence-backed, reasonably priced opportunities."
        ),
    },
    {
        "id": "aggressive",
        "name": "Aggressive Upside Analyst",
        "prompt": (
            "You are an aggressive VC analyst. Tolerate risk if asymmetrical upside exists. "
            "Prioritize category-defining growth potential."
        ),
    },
]


# ---------------------------------------------------------------------------
# Isolated per-agent state
# ---------------------------------------------------------------------------


@dataclass
class AgentDecision:
    """Single decision produced by one agent for one deal."""

    shadow_cycle_item_id: str
    company_name: str
    analyst_profile: str
    recommendation_class: str
    conviction: int
    rationale: str
    key_risks: list[str]
    data_gaps: list[str]
    raw_response: dict[str, Any]
    model_recommendation: str | None = None
    is_fallback: bool = False
    fallback_reason: str = ""


@dataclass
class AgentState:
    """Isolated mutable state owned by a single agent worker."""

    profile: dict[str, str]
    decisions: list[AgentDecision] = field(default_factory=list)
    errors: int = 0


# ---------------------------------------------------------------------------
# JSON / context helpers (shared read-only)
# ---------------------------------------------------------------------------


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _quarter_label(d: date) -> str:
    q = ((d.month - 1) // 3) + 1
    return f"{d.year}-Q{q}"


def _parse_json_response(text: str) -> dict[str, Any]:
    """Parse a JSON object from Claude's response text."""
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        raise ValueError("Claude response did not contain a JSON object.")
    parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("Claude response JSON was not an object.")
    return parsed


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return len(value) > 0
    return True


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        out: list[str] = []
        for entry in value:
            text = str(entry).strip()
            if text:
                out.append(text)
        return out[:8]
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    return []


def _compute_data_sufficiency(item: dict[str, Any]) -> dict[str, Any]:
    """Compute evidence-quality score and missing contract fields."""
    identity_fields = {
        "company_name": _has_value(item.get("company_name")),
        "country": _has_value(item.get("country")),
        "campaign_date": _has_value(item.get("campaign_date")),
        "stage_bucket": _has_value(item.get("stage_bucket")),
    }
    financial_fields = {
        "had_revenue": _has_value(item.get("had_revenue")),
        "revenue_at_raise": _has_value(item.get("revenue_at_raise")),
        "revenue_growth_yoy": _has_value(item.get("revenue_growth_yoy")),
        "total_assets": _has_value(item.get("total_assets")),
        "total_debt": _has_value(item.get("total_debt")),
        "burn_rate_monthly": _has_value(item.get("burn_rate_monthly")),
    }
    terms_fields = {
        "funding_target": _has_value(item.get("funding_target")),
        "amount_raised": _has_value(item.get("amount_raised")),
        "pre_money_valuation": _has_value(item.get("pre_money_valuation")),
        "equity_offered": _has_value(item.get("equity_offered")),
        "instrument_type": _has_value(item.get("instrument_type")),
    }
    team_traction_fields = {
        "founder_count": _has_value(item.get("founder_count")),
        "employee_count": _has_value(item.get("employee_count")),
        "investor_count": _has_value(item.get("cf_investor_count")),
        "company_age_months": _has_value(item.get("company_age_at_raise_months")),
    }
    narrative_fields = {
        "text_quality_score": _has_value(item.get("text_quality_score")),
        "narrative_excerpt": _has_value(item.get("narrative_excerpt")),
    }

    def _category_score(field_map: dict[str, bool], weight: int) -> tuple[int, int]:
        present = sum(1 for v in field_map.values() if v)
        total = len(field_map)
        if total == 0:
            return 0, 0
        scaled = int(round((present / total) * weight))
        return scaled, present

    identity_score, identity_present = _category_score(identity_fields, 20)
    financial_score, financial_present = _category_score(financial_fields, 20)
    terms_score, terms_present = _category_score(terms_fields, 20)
    team_score, team_present = _category_score(team_traction_fields, 20)
    narrative_score, narrative_present = _category_score(narrative_fields, 20)

    quality_bonus = 0
    if _safe_float(item.get("field_completeness_ratio")) is not None:
        quality_bonus += min(
            10,
            int(round((_safe_float(item.get("field_completeness_ratio")) or 0.0) * 10)),
        )
    elif _safe_float(item.get("data_source_count")) is not None:
        quality_bonus += min(10, int(_safe_float(item.get("data_source_count")) or 0))

    total_score = min(
        100,
        identity_score
        + financial_score
        + terms_score
        + team_score
        + narrative_score
        + quality_bonus,
    )

    required_contract_fields = {
        "campaign_date": _has_value(item.get("campaign_date")),
        "amount_or_target": _has_value(item.get("amount_raised"))
        or _has_value(item.get("funding_target")),
        "revenue_or_flag": _has_value(item.get("revenue_at_raise"))
        or _has_value(item.get("had_revenue")),
        "team_or_traction": _has_value(item.get("founder_count"))
        or _has_value(item.get("employee_count"))
        or _has_value(item.get("cf_investor_count")),
        "narrative_or_text_score": _has_value(item.get("narrative_excerpt"))
        or _has_value(item.get("text_quality_score")),
    }
    missing_required = [k for k, ok in required_contract_fields.items() if not ok]

    categories = {
        "identity": identity_score,
        "financial": financial_score,
        "terms": terms_score,
        "team_traction": team_score,
        "narrative": narrative_score,
    }
    category_count = sum(1 for s in categories.values() if s >= 10)

    source_map = {
        "campaign_source": item.get("cf_data_source"),
        "campaign_as_of": item.get("campaign_date"),
        "financial_source": item.get("financial_source"),
        "financial_as_of": item.get("financial_period_end_date"),
        "text_source": item.get("text_source"),
        "text_as_of": item.get("text_filing_date"),
        "feature_as_of": item.get("feature_as_of_date"),
    }

    return {
        "score": total_score,
        "category_count": category_count,
        "categories": categories,
        "missing_required_fields": missing_required,
        "source_map": source_map,
    }


def _has_actionable_context(context: dict[str, Any]) -> bool:
    model_output = context.get("model_output", {})
    if model_output.get("score") is not None:
        return True
    suff = context.get("data_sufficiency") or {}
    return (
        (_safe_float(suff.get("score")) or 0.0) >= 45.0
        and int(suff.get("category_count") or 0) >= 2
    )


def _persona_fallback_from_context(
    persona_id: str,
    context: dict[str, Any],
) -> tuple[str, int]:
    deal = context.get("deal_snapshot", {})
    latest_round = deal.get("latest_round", {})
    score = 0
    if deal.get("amount_raised") not in (None, 0, 0.0):
        score += 1
    if latest_round.get("amount_raised") not in (None, 0, 0.0):
        score += 1
    if deal.get("had_revenue") is True:
        score += 2
    elif deal.get("had_revenue") is False:
        score -= 1
    if deal.get("stage_bucket") in {"seed", "early_growth"}:
        score += 1

    if persona_id == "conservative":
        if score >= 3:
            return "deep_diligence", 55
        if score >= 1:
            return "watch", 45
        return "pass", 35

    if persona_id == "balanced":
        if score >= 4:
            return "invest", 70
        if score >= 2:
            return "deep_diligence", 58
        if score >= 1:
            return "watch", 48
        return "pass", 38

    # aggressive
    if score >= 4:
        return "invest", 78
    if score >= 2:
        return "deep_diligence", 64
    return "watch", 52


def _build_context(item: dict[str, Any]) -> dict[str, Any]:
    suff = item.get("data_sufficiency") or _compute_data_sufficiency(item)
    return {
        "company": {
            "name": item.get("company_name"),
            "sector": item.get("sector"),
            "country": item.get("country"),
            "source": item.get("source"),
        },
        "model_output": {
            "recommendation": item.get("model_recommendation"),
            "score": item.get("score"),
            "confidence_level": item.get("confidence_level"),
            "category_scores": item.get("category_scores") or {},
            "risk_flags": item.get("risk_flags") or [],
            "missing_data_fields": item.get("missing_data_fields") or [],
            "valuation_analysis": item.get("valuation_analysis") or {},
            "return_distribution": item.get("return_distribution") or {},
        },
        "data_sufficiency": {
            "score": suff.get("score"),
            "category_count": suff.get("category_count"),
            "category_breakdown": suff.get("categories") or {},
            "missing_required_fields": suff.get("missing_required_fields") or [],
        },
        "provenance": suff.get("source_map") or {},
        "deal_snapshot": {
            "platform": item.get("cf_platform"),
            "campaign_date": item.get("campaign_date"),
            "funding_target": item.get("funding_target"),
            "amount_raised": item.get("amount_raised"),
            "pre_money_valuation": item.get("pre_money_valuation"),
            "overfunding_ratio": item.get("overfunding_ratio"),
            "equity_offered": item.get("equity_offered"),
            "investor_count": item.get("cf_investor_count"),
            "had_revenue": item.get("had_revenue"),
            "revenue_at_raise": item.get("revenue_at_raise"),
            "founder_count": item.get("founder_count"),
            "company_age_months": item.get("company_age_at_raise_months"),
            "stage_bucket": item.get("stage_bucket"),
            "financials": {
                "as_of_date": item.get("financial_period_end_date"),
                "revenue_growth_yoy": item.get("revenue_growth_yoy"),
                "employee_count": item.get("employee_count"),
                "burn_rate_monthly": item.get("burn_rate_monthly"),
                "total_assets": item.get("total_assets"),
                "total_debt": item.get("total_debt"),
            },
            "narrative": {
                "text_quality_score": item.get("text_quality_score"),
                "excerpt": item.get("narrative_excerpt"),
            },
            "latest_round": {
                "date": item.get("round_date"),
                "type": item.get("round_type"),
                "instrument_type": item.get("instrument_type"),
                "amount_raised": item.get("round_amount_raised"),
                "pre_money_valuation": item.get("round_pre_money_valuation"),
                "investor_count": item.get("round_investor_count"),
                "qualified_institutional": item.get("qualified_institutional"),
                "eis_seis_eligible": item.get("eis_seis_eligible"),
                "qsbs_eligible": item.get("qsbs_eligible"),
            },
        },
    }


def _heuristic_seed_score(item: dict[str, Any]) -> float:
    """Deterministic seed score for model-alignment coverage on sparse cycles."""
    suff = item.get("data_sufficiency") or _compute_data_sufficiency(item)
    score = 35.0 + (float(suff.get("score") or 0.0) - 50.0) * 0.35

    had_revenue = item.get("had_revenue")
    revenue = _safe_float(item.get("revenue_at_raise"))
    valuation = _safe_float(item.get("pre_money_valuation"))
    overfunding = _safe_float(item.get("overfunding_ratio"))
    rev_growth = _safe_float(item.get("revenue_growth_yoy"))
    institutional = bool(item.get("qualified_institutional"))
    disq_count = _safe_float(item.get("director_disqualifications")) or 0.0
    overdue = bool(item.get("accounts_overdue"))

    if had_revenue is True:
        score += 10.0
    elif had_revenue is False:
        score -= 8.0

    if revenue is not None and revenue > 0:
        score += 6.0
    if rev_growth is not None:
        if rev_growth > 0.25:
            score += 6.0
        elif rev_growth < -0.10:
            score -= 6.0

    if institutional:
        score += 10.0
    if overfunding is not None and overfunding > 1.15:
        score += 5.0

    if valuation is not None and revenue is not None and revenue > 0:
        multiple = valuation / revenue
        if multiple > 40:
            score -= 12.0
        elif multiple > 25:
            score -= 8.0
        elif multiple < 8:
            score += 3.0

    if disq_count > 0:
        score -= 25.0
    if overdue:
        score -= 12.0

    return max(0.0, min(100.0, round(score, 2)))


def _seed_recommendation_class(item: dict[str, Any]) -> str:
    suff = item.get("data_sufficiency") or _compute_data_sufficiency(item)
    suff_score = float(suff.get("score") or 0.0)
    if suff_score < 45 or int(suff.get("category_count") or 0) < 2:
        return "abstain"
    score = _heuristic_seed_score(item)
    if score >= 72:
        return "invest"
    if score >= 58:
        return "deep_diligence"
    if score >= 42:
        return "watch"
    return "pass"


def _inferred_recommendation_class(item: dict[str, Any]) -> str:
    rec = (item.get("model_recommendation") or "").strip().lower()
    if rec in RECOMMENDATIONS:
        return rec
    return _seed_recommendation_class(item)


def _missing_field_histogram(items: list[dict[str, Any]]) -> list[tuple[str, int]]:
    counts: Counter[str] = Counter()
    for item in items:
        suff = item.get("data_sufficiency") or _compute_data_sufficiency(item)
        for field_name in suff.get("missing_required_fields") or []:
            counts[field_name] += 1
    return counts.most_common(8)


def _select_pilot_items(
    items: list[dict[str, Any]],
    max_items: int,
    min_class_coverage: int,
    min_model_coverage: float,
) -> list[dict[str, Any]]:
    """Select a deterministic, class-diverse pilot subset."""
    if not items:
        return []

    ranked = sorted(
        items,
        key=lambda x: (
            -(x.get("data_sufficiency", {}).get("score") or 0),
            -(1 if x.get("model_recommendation") else 0),
            str(x.get("company_name") or ""),
        ),
    )

    target_n = min(max_items, len(ranked))
    target_model_items = int(math.ceil(target_n * min_model_coverage))

    by_class: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in ranked:
        by_class[_inferred_recommendation_class(item)].append(item)

    selected: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    # Step 1: force class diversity.
    for klass, bucket in sorted(
        by_class.items(), key=lambda kv: (-len(kv[1]), kv[0])
    )[:min_class_coverage]:
        _ = klass
        if bucket:
            candidate = bucket[0]
            item_id = str(candidate.get("shadow_cycle_item_id"))
            if item_id not in seen_ids:
                selected.append(candidate)
                seen_ids.add(item_id)

    # Step 2: ensure model-linked coverage.
    model_selected = sum(1 for i in selected if i.get("model_recommendation"))
    if model_selected < target_model_items:
        for item in ranked:
            if model_selected >= target_model_items or len(selected) >= target_n:
                break
            item_id = str(item.get("shadow_cycle_item_id"))
            if item_id in seen_ids or not item.get("model_recommendation"):
                continue
            selected.append(item)
            seen_ids.add(item_id)
            model_selected += 1

    # Step 3: fill remaining by score.
    for item in ranked:
        if len(selected) >= target_n:
            break
        item_id = str(item.get("shadow_cycle_item_id"))
        if item_id in seen_ids:
            continue
        selected.append(item)
        seen_ids.add(item_id)

    return selected


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _load_cycle(conn, cycle_id: str | None) -> dict[str, Any]:
    if cycle_id:
        rows = execute_query(
            conn,
            """
            SELECT id::text, cycle_name, target_count, status, started_at::text
            FROM shadow_cycles
            WHERE id = %s::uuid
            LIMIT 1
            """,
            (cycle_id,),
        )
    else:
        rows = execute_query(
            conn,
            """
            SELECT id::text, cycle_name, target_count, status, started_at::text
            FROM shadow_cycles
            WHERE status = 'active'
            ORDER BY started_at DESC
            LIMIT 1
            """,
        )
    if not rows:
        raise typer.BadParameter("No matching shadow cycle found.")
    return rows[0]


def _annotate_cycle_items(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        if (
            item.get("overfunding_ratio") is None
            and _safe_float(item.get("amount_raised")) is not None
            and _safe_float(item.get("funding_target")) not in (None, 0.0)
        ):
            raised = _safe_float(item.get("amount_raised")) or 0.0
            target = _safe_float(item.get("funding_target")) or 0.0
            if target > 0:
                item["overfunding_ratio"] = round(raised / target, 4)
        item["data_sufficiency"] = _compute_data_sufficiency(item)
        enriched.append(item)
    return enriched


def _cycle_items_query(
    id_filter_sql: str,
    limit_sql: str,
) -> str:
    return f"""
        WITH base_items AS (
          SELECT
            sci.id,
            sci.entity_id,
            sci.company_name,
            COALESCE(sci.sector, ce.sector) AS sector,
            COALESCE(sci.country, ce.country, 'US') AS country,
            sci.source,
            sci.source_ref,
            sci.evaluation_id AS shadow_evaluation_id,
            sci.created_at,
            sci.created_at::date AS created_date
          FROM shadow_cycle_items sci
          LEFT JOIN canonical_entities ce ON ce.id = sci.entity_id
          WHERE sci.cycle_id = %s::uuid
          {id_filter_sql}
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
        eval_direct AS (
          SELECT
            ec.id AS item_id,
            e.id::text AS eval_id,
            e.recommendation_class,
            e.quantitative_score,
            e.confidence_level,
            e.category_scores,
            e.risk_flags,
            e.missing_data_fields,
            e.valuation_analysis,
            e.return_distribution
          FROM enrichment_company ec
          LEFT JOIN evaluations e ON e.id = ec.shadow_evaluation_id
        ),
        latest_eval AS (
          SELECT
            ec.id AS item_id,
            e.id::text AS eval_id,
            e.recommendation_class,
            e.quantitative_score,
            e.confidence_level,
            e.category_scores,
            e.risk_flags,
            e.missing_data_fields,
            e.valuation_analysis,
            e.return_distribution
          FROM enrichment_company ec
          LEFT JOIN LATERAL (
            SELECT
              e.id,
              e.recommendation_class,
              e.quantitative_score,
              e.confidence_level,
              e.category_scores,
              e.risk_flags,
              e.missing_data_fields,
              e.valuation_analysis,
              e.return_distribution
            FROM evaluations e
            WHERE
              (ec.entity_id IS NOT NULL AND e.entity_id = ec.entity_id)
              OR lower(e.company_name) = lower(ec.company_name)
            ORDER BY
              CASE WHEN ec.entity_id IS NOT NULL AND e.entity_id = ec.entity_id THEN 0 ELSE 1 END,
              e.created_at DESC
            LIMIT 1
          ) e ON true
        ),
        campaign AS (
          SELECT
            ec.id AS item_id,
            co.platform AS cf_platform,
            co.sector AS campaign_sector,
            co.campaign_date,
            co.funding_target,
            co.amount_raised,
            co.overfunding_ratio,
            co.pre_money_valuation,
            co.equity_offered,
            co.investor_count AS cf_investor_count,
            co.had_revenue,
            co.revenue_at_raise,
            co.founder_count,
            co.company_age_at_raise_months,
            co.stage_bucket,
            co.label_quality_tier AS cf_label_quality_tier,
            co.data_source AS cf_data_source
          FROM enrichment_company ec
          LEFT JOIN LATERAL (
            SELECT co.*
            FROM crowdfunding_outcomes co
            WHERE co.company_id = ec.company_id
            ORDER BY co.campaign_date DESC NULLS LAST
            LIMIT 1
          ) co ON true
        ),
        rounds AS (
          SELECT
            ec.id AS item_id,
            fr.round_date,
            fr.round_type,
            fr.instrument_type,
            fr.amount_raised AS round_amount_raised,
            fr.pre_money_valuation AS round_pre_money_valuation,
            fr.investor_count AS round_investor_count,
            fr.qualified_institutional,
            fr.eis_seis_eligible,
            fr.qsbs_eligible
          FROM enrichment_company ec
          LEFT JOIN campaign ca ON ca.item_id = ec.id
          LEFT JOIN LATERAL (
            SELECT fr.*
            FROM funding_rounds fr
            WHERE fr.company_id = ec.company_id
              AND (
                ca.campaign_date IS NULL
                OR fr.round_date IS NULL
                OR fr.round_date <= ca.campaign_date
              )
            ORDER BY fr.round_date DESC NULLS LAST
            LIMIT 1
          ) fr ON true
        ),
        financial AS (
          SELECT
            ec.id AS item_id,
            fd.period_end_date AS financial_period_end_date,
            fd.revenue AS financial_revenue,
            fd.revenue_growth_yoy,
            fd.employee_count,
            fd.burn_rate_monthly,
            fd.total_assets,
            fd.total_debt,
            fd.source_filing AS financial_source
          FROM enrichment_company ec
          LEFT JOIN campaign ca ON ca.item_id = ec.id
          LEFT JOIN rounds ro ON ro.item_id = ec.id
          LEFT JOIN LATERAL (
            SELECT fd.*
            FROM financial_data fd
            WHERE fd.company_id = ec.company_id
              AND fd.period_end_date <= COALESCE(ca.campaign_date, ro.round_date, ec.created_date)
            ORDER BY fd.period_end_date DESC NULLS LAST
            LIMIT 1
          ) fd ON true
        ),
        text_profile AS (
          SELECT
            ec.id AS item_id,
            tx.filing_date AS text_filing_date,
            tx.text_quality_score,
            tx.red_flags,
            tx.narrative_excerpt,
            'sec_form_c_texts'::text AS text_source
          FROM enrichment_company ec
          LEFT JOIN campaign ca ON ca.item_id = ec.id
          LEFT JOIN rounds ro ON ro.item_id = ec.id
          LEFT JOIN LATERAL (
            SELECT
              sft.filing_date,
              cts.text_quality_score,
              cts.red_flags,
              LEFT(sft.narrative_text, 900) AS narrative_excerpt,
              cts.created_at
            FROM sec_form_c_texts sft
            LEFT JOIN claude_text_scores cts ON cts.form_c_text_id = sft.id
            WHERE sft.company_id = ec.company_id
              AND COALESCE(sft.filing_date, ec.created_date) <= COALESCE(
                ca.campaign_date,
                ro.round_date,
                ec.created_date
              )
            ORDER BY sft.filing_date DESC NULLS LAST, cts.created_at DESC NULLS LAST
            LIMIT 1
          ) tx ON true
        ),
        feature_latest AS (
          SELECT
            ec.id AS item_id,
            tfw.as_of_date AS feature_as_of_date,
            tfw.data_source_count,
            tfw.field_completeness_ratio,
            tfw.director_disqualifications,
            tfw.accounts_overdue,
            tfw.company_status
          FROM enrichment_company ec
          LEFT JOIN campaign ca ON ca.item_id = ec.id
          LEFT JOIN rounds ro ON ro.item_id = ec.id
          LEFT JOIN LATERAL (
            SELECT tfw.*
            FROM training_features_wide tfw
            WHERE tfw.entity_id = ec.entity_id
              AND tfw.as_of_date <= COALESCE(ca.campaign_date, ro.round_date, ec.created_date)
            ORDER BY tfw.as_of_date DESC
            LIMIT 1
          ) tfw ON true
        ),
        company_meta AS (
          SELECT
            ec.id AS item_id,
            c.sector AS company_sector
          FROM enrichment_company ec
          LEFT JOIN companies c ON c.id = ec.company_id
        )
        SELECT
          ec.id::text AS shadow_cycle_item_id,
          ec.entity_id::text AS entity_id,
          ec.company_name,
          COALESCE(ec.sector, ca.campaign_sector, cm.company_sector) AS sector,
          ec.country,
          ec.source,
          ec.source_ref,
          COALESCE(ed.eval_id, le.eval_id) AS evaluation_id,
          COALESCE(ed.recommendation_class, le.recommendation_class) AS model_recommendation,
          COALESCE(ed.quantitative_score, le.quantitative_score) AS score,
          COALESCE(ed.confidence_level, le.confidence_level) AS confidence_level,
          COALESCE(ed.category_scores, le.category_scores) AS category_scores,
          COALESCE(ed.risk_flags, le.risk_flags) AS risk_flags,
          COALESCE(ed.missing_data_fields, le.missing_data_fields) AS missing_data_fields,
          COALESCE(ed.valuation_analysis, le.valuation_analysis) AS valuation_analysis,
          COALESCE(ed.return_distribution, le.return_distribution) AS return_distribution,
          ca.cf_platform,
          ca.campaign_date,
          COALESCE(ca.funding_target, ro.round_amount_raised) AS funding_target,
          COALESCE(ca.amount_raised, ro.round_amount_raised) AS amount_raised,
          ca.overfunding_ratio,
          COALESCE(ca.pre_money_valuation, ro.round_pre_money_valuation) AS pre_money_valuation,
          ca.equity_offered,
          COALESCE(ca.cf_investor_count, ro.round_investor_count) AS cf_investor_count,
          COALESCE(
            ca.had_revenue,
            CASE
              WHEN ca.revenue_at_raise IS NOT NULL THEN ca.revenue_at_raise > 0
              WHEN fin.financial_revenue IS NOT NULL THEN fin.financial_revenue > 0
              ELSE NULL
            END
          ) AS had_revenue,
          COALESCE(ca.revenue_at_raise, fin.financial_revenue) AS revenue_at_raise,
          ca.founder_count,
          ca.company_age_at_raise_months,
          ca.stage_bucket,
          ca.cf_label_quality_tier,
          ca.cf_data_source,
          ro.round_date,
          ro.round_type,
          ro.instrument_type,
          ro.round_amount_raised,
          ro.round_pre_money_valuation,
          ro.round_investor_count,
          ro.qualified_institutional,
          ro.eis_seis_eligible,
          ro.qsbs_eligible,
          fin.financial_period_end_date,
          fin.revenue_growth_yoy,
          fin.employee_count,
          fin.burn_rate_monthly,
          fin.total_assets,
          fin.total_debt,
          fin.financial_source,
          tp.text_filing_date,
          tp.text_quality_score,
          tp.red_flags,
          tp.narrative_excerpt,
          tp.text_source,
          fl.feature_as_of_date,
          fl.data_source_count,
          fl.field_completeness_ratio,
          fl.director_disqualifications,
          fl.accounts_overdue,
          fl.company_status
        FROM enrichment_company ec
        LEFT JOIN eval_direct ed ON ed.item_id = ec.id
        LEFT JOIN latest_eval le ON le.item_id = ec.id
        LEFT JOIN campaign ca ON ca.item_id = ec.id
        LEFT JOIN rounds ro ON ro.item_id = ec.id
        LEFT JOIN financial fin ON fin.item_id = ec.id
        LEFT JOIN text_profile tp ON tp.item_id = ec.id
        LEFT JOIN feature_latest fl ON fl.item_id = ec.id
        LEFT JOIN company_meta cm ON cm.item_id = ec.id
        ORDER BY (COALESCE(ed.eval_id, le.eval_id) IS NULL) ASC, ec.created_at ASC
        {limit_sql}
    """


def _load_cycle_items(conn, cycle_id: str, max_items: int) -> list[dict[str, Any]]:
    rows = execute_query(
        conn,
        _cycle_items_query("", "LIMIT %s"),
        (cycle_id, max_items),
    )
    return _annotate_cycle_items(rows)


def _load_cycle_items_by_ids(
    conn, cycle_id: str, item_ids: list[str]
) -> list[dict[str, Any]]:
    """Load specific shadow-cycle items by ID, preserving input order."""
    if not item_ids:
        return []

    placeholders = ", ".join(["%s::uuid"] * len(item_ids))
    rows = execute_query(
        conn,
        _cycle_items_query(f"AND sci.id IN ({placeholders})", ""),
        (cycle_id, *item_ids),
    )
    annotated = _annotate_cycle_items(rows)
    by_id = {r["shadow_cycle_item_id"]: r for r in annotated}
    return [by_id[iid] for iid in item_ids if iid in by_id]


def _seed_confidence_bounds(
    sufficiency_score: float,
    base_score: float,
) -> tuple[str, float, float]:
    if sufficiency_score >= 80:
        spread = 8.0
        level = "high"
    elif sufficiency_score >= 65:
        spread = 15.0
        level = "moderate"
    else:
        spread = 25.0
        level = "low"
    return level, max(0.0, base_score - spread), min(100.0, base_score + spread)


def _seed_category_scores(item: dict[str, Any], score: float) -> dict[str, float]:
    suff = item.get("data_sufficiency") or _compute_data_sufficiency(item)
    categories = suff.get("categories") or {}
    # Map pilot evidence categories to rubric-like output expected by consumers.
    return {
        "Text & Narrative": float(categories.get("narrative", 0)),
        "Traction & Growth": float(categories.get("financial", 0)),
        "Deal Terms": float(categories.get("terms", 0)),
        "Team": float(categories.get("team_traction", 0)),
        "Financial Health": float(categories.get("financial", 0)),
        "Investment Signal": float(score),
        "Market": float(categories.get("identity", 0)),
    }


def _seed_missing_model_evaluations(
    conn,
    items: list[dict[str, Any]],
    min_model_coverage: float,
) -> int:
    """Seed deterministic model-linked evaluations for alignment coverage."""
    if not items:
        return 0

    target_with_model = int(math.ceil(len(items) * min_model_coverage))
    current_with_model = sum(1 for item in items if item.get("model_recommendation"))
    to_seed = max(0, target_with_model - current_with_model)
    if to_seed == 0:
        return 0

    seed_candidates = [
        i for i in items if not i.get("model_recommendation")
    ]
    seed_candidates.sort(
        key=lambda x: (
            -(x.get("data_sufficiency", {}).get("score") or 0),
            str(x.get("company_name") or ""),
        ),
    )

    seeded = 0
    for item in seed_candidates[:to_seed]:
        suff = item.get("data_sufficiency") or _compute_data_sufficiency(item)
        suff_score = float(suff.get("score") or 0.0)
        quant_score = _heuristic_seed_score(item)
        rec = _seed_recommendation_class(item)
        confidence_level, lower, upper = _seed_confidence_bounds(suff_score, quant_score)
        category_scores = _seed_category_scores(item, quant_score)

        manual_inputs = {
            "seeded": True,
            "seed_note": SEED_EVAL_NOTE,
            "seed_source": "synthetic_shadow_cycle",
            "campaign_date": item.get("campaign_date"),
            "funding_target": item.get("funding_target"),
            "amount_raised": item.get("amount_raised"),
            "pre_money_valuation": item.get("pre_money_valuation"),
            "instrument_type": item.get("instrument_type"),
            "had_revenue": item.get("had_revenue"),
            "revenue_at_raise": item.get("revenue_at_raise"),
            "text_quality_score": item.get("text_quality_score"),
        }

        abstention_gates = {
            "data_sufficiency": {
                "passed": suff_score >= DEFAULT_MIN_SUFFICIENCY_SCORE,
                "value": suff_score,
                "threshold": DEFAULT_MIN_SUFFICIENCY_SCORE,
            },
            "category_count": {
                "passed": int(suff.get("category_count") or 0) >= DEFAULT_MIN_CATEGORY_COUNT,
                "value": int(suff.get("category_count") or 0),
                "threshold": DEFAULT_MIN_CATEGORY_COUNT,
            },
        }

        result = execute_query(
            conn,
            """
            INSERT INTO evaluations (
              evaluation_type, entity_id, entity_match_confidence, company_name,
              manual_inputs, quantitative_score, confidence_lower, confidence_upper,
              confidence_level, category_scores, risk_flags, missing_data_fields,
              abstention_gates, recommendation_class, quick_recommendation, notes
            )
            VALUES (
              'quick', %s::uuid, %s, %s,
              %s::jsonb, %s, %s, %s,
              %s, %s::jsonb, %s::jsonb, %s::jsonb,
              %s::jsonb, %s, %s, %s
            )
            RETURNING id::text
            """,
            (
                item.get("entity_id"),
                85,
                item.get("company_name"),
                json.dumps(manual_inputs, default=_json_default),
                quant_score,
                lower,
                upper,
                confidence_level,
                json.dumps(category_scores, default=_json_default),
                json.dumps([], default=_json_default),
                json.dumps(suff.get("missing_required_fields") or [], default=_json_default),
                json.dumps(abstention_gates, default=_json_default),
                rec,
                rec,
                SEED_EVAL_NOTE,
            ),
        )
        evaluation_id = result[0]["id"]

        execute_query(
            conn,
            """
            UPDATE shadow_cycle_items
            SET evaluation_id = %s::uuid, recommendation_class = %s
            WHERE id = %s::uuid
            """,
            (evaluation_id, rec, item["shadow_cycle_item_id"]),
        )

        item["evaluation_id"] = evaluation_id
        item["model_recommendation"] = rec
        item["score"] = quant_score
        item["confidence_level"] = confidence_level
        item["category_scores"] = category_scores
        seeded += 1

    if seeded > 0:
        conn.commit()
    return seeded


def _upsert_decision(conn, run_id: str, decision: AgentDecision) -> None:
    """Idempotent upsert on (run_id, shadow_cycle_item_id, analyst_profile)."""
    # Merge fallback metadata into raw_response for audit trail
    raw = dict(decision.raw_response)
    raw["is_fallback"] = decision.is_fallback
    raw["fallback_reason"] = decision.fallback_reason
    raw["model_recommendation"] = decision.model_recommendation

    execute_query(
        conn,
        """
        INSERT INTO synthetic_pilot_decisions (
          run_id, shadow_cycle_item_id, analyst_profile,
          recommendation_class, conviction, rationale,
          key_risks, data_gaps, raw_response, created_at
        )
        VALUES (
          %s::uuid, %s::uuid, %s, %s, %s, %s,
          %s::jsonb, %s::jsonb, %s::jsonb, now()
        )
        ON CONFLICT (run_id, shadow_cycle_item_id, analyst_profile)
        DO UPDATE SET
          recommendation_class = EXCLUDED.recommendation_class,
          conviction = EXCLUDED.conviction,
          rationale = EXCLUDED.rationale,
          key_risks = EXCLUDED.key_risks,
          data_gaps = EXCLUDED.data_gaps,
          raw_response = EXCLUDED.raw_response,
          created_at = now()
        """,
        (
            run_id,
            decision.shadow_cycle_item_id,
            decision.analyst_profile,
            decision.recommendation_class,
            decision.conviction,
            decision.rationale,
            json.dumps(decision.key_risks, default=_json_default),
            json.dumps(decision.data_gaps, default=_json_default),
            json.dumps(raw, default=_json_default),
        ),
    )


# ---------------------------------------------------------------------------
# Async agent worker
# ---------------------------------------------------------------------------


async def _ask_persona_async(
    client: anthropic.AsyncAnthropic,
    persona: dict[str, str],
    context: dict[str, Any],
    *,
    strict_no_fallback: bool = False,
    temperature: float = 0.2,
) -> dict[str, Any]:
    """Call Claude API for a single persona + deal.  1 retry on malformed JSON."""
    system = (
        persona["prompt"]
        + "\n\n"
        + (
            "Return strict JSON with keys: recommendation_class, conviction, rationale, "
            "key_risks, data_gaps."
        )
        + "\nrecommendation_class must be one of: invest, deep_diligence, watch, pass, abstain."
        + "\nconviction must be an integer 0-100."
        + "\nUse abstain only when evidence is materially insufficient. "
        + "If model_output or deal_snapshot has usable data, return a non-abstain class."
    )
    user = "Deal context:\n" + json.dumps(context, default=_json_default)

    parsed: dict[str, Any] | None = None
    last_error: Exception | None = None

    for attempt in range(2):
        user_message = user
        if attempt == 1:
            user_message += (
                "\n\nIMPORTANT: Return JSON object only. No prose outside JSON. "
                "No markdown fences."
            )
        try:
            msg = await client.messages.create(
                model=MODEL,
                max_tokens=500,
                temperature=temperature,
                system=system,
                messages=[{"role": "user", "content": user_message}],
            )
            text = ""
            for block in msg.content:
                if getattr(block, "type", None) == "text":
                    text += block.text
            parsed = _parse_json_response(text)
            break
        except Exception as exc:
            last_error = exc
            continue

    if parsed is None:
        raise ValueError("Claude response parsing failed.") from last_error

    rec = str(parsed.get("recommendation_class", "abstain")).strip().lower()
    invalid_recommendation = rec not in RECOMMENDATIONS
    if rec not in RECOMMENDATIONS:
        rec = "abstain"

    conviction_raw = parsed.get("conviction", 0)
    try:
        conviction = int(conviction_raw)
    except (TypeError, ValueError):
        conviction = 0
    conviction = max(0, min(100, conviction))

    is_fallback = False
    fallback_reason = ""

    if rec == "abstain" and _has_actionable_context(context) and not strict_no_fallback:
        fallback_rec, fallback_conviction = _persona_fallback_from_context(
            persona["id"], context
        )
        rec = fallback_rec
        if conviction == 0:
            conviction = fallback_conviction
        parsed["fallback_from_abstain"] = True
        is_fallback = True
        fallback_reason = (
            "invalid_recommendation"
            if invalid_recommendation
            else "abstain_with_actionable_context"
        )

    return {
        "recommendation_class": rec,
        "conviction": conviction,
        "rationale": str(parsed.get("rationale", "No rationale provided")).strip(),
        "key_risks": _normalize_string_list(parsed.get("key_risks")),
        "data_gaps": _normalize_string_list(parsed.get("data_gaps")),
        "raw": parsed,
        "is_fallback": is_fallback,
        "fallback_reason": fallback_reason,
    }


async def _run_agent(
    client: anthropic.AsyncAnthropic,
    agent_state: AgentState,
    items: list[dict[str, Any]],
    *,
    strict_no_fallback: bool = False,
    temperature: float = 0.2,
) -> None:
    """Run a single agent across all deals.  Mutates only its own AgentState."""
    persona = agent_state.profile

    for item in items:
        context = _build_context(item)
        try:
            result = await _ask_persona_async(
                client,
                persona,
                context,
                strict_no_fallback=strict_no_fallback,
                temperature=temperature,
            )
        except Exception as exc:
            logger.warning(
                "agent_persona_failed",
                company_name=item.get("company_name"),
                persona=persona["id"],
                error=str(exc),
            )
            fallback_reason = (
                "parse_error"
                if isinstance(exc, ValueError)
                else "api_error"
            )
            result = {
                "recommendation_class": "abstain",
                "conviction": 0,
                "rationale": "Persona scoring failed; defaulted to abstain.",
                "key_risks": ["persona_scoring_error"],
                "data_gaps": ["claude_response_unavailable"],
                "raw": {"error": str(exc)},
                "is_fallback": False,
                "fallback_reason": fallback_reason,
            }
            agent_state.errors += 1

        agent_state.decisions.append(
            AgentDecision(
                shadow_cycle_item_id=item["shadow_cycle_item_id"],
                company_name=item["company_name"],
                analyst_profile=persona["id"],
                recommendation_class=result["recommendation_class"],
                conviction=result["conviction"],
                rationale=result["rationale"],
                key_risks=result["key_risks"],
                data_gaps=result["data_gaps"],
                raw_response=result["raw"],
                model_recommendation=item.get("model_recommendation"),
                is_fallback=result.get("is_fallback", False),
                fallback_reason=result.get("fallback_reason", ""),
            )
        )


async def _fan_out_agents(
    api_key: str,
    items: list[dict[str, Any]],
    *,
    strict_no_fallback: bool = False,
    temperature: float = 0.2,
) -> list[AgentState]:
    """Launch 3 independent agent workers in parallel, return their states."""
    # Each agent gets its OWN async client instance and state object.
    agents: list[AgentState] = []
    tasks: list[asyncio.Task] = []

    for profile in AGENT_PROFILES:
        state = AgentState(profile=profile)
        agents.append(state)
        client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=2)
        tasks.append(
            asyncio.create_task(
                _run_agent(
                    client,
                    state,
                    items,
                    strict_no_fallback=strict_no_fallback,
                    temperature=temperature,
                )
            )
        )

    await asyncio.gather(*tasks)
    return agents


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _aggregate(
    run_name: str,
    cycle: dict[str, Any],
    agent_states: list[AgentState],
) -> dict[str, Any]:
    """Build summary metrics from isolated agent states."""
    all_decisions: list[AgentDecision] = []
    per_agent_dist: dict[str, dict[str, int]] = {}

    for state in agent_states:
        per_agent_dist[state.profile["id"]] = dict(
            Counter(d.recommendation_class for d in state.decisions)
        )
        all_decisions.extend(state.decisions)

    by_item: dict[str, list[AgentDecision]] = defaultdict(list)
    for d in all_decisions:
        by_item[d.shadow_cycle_item_id].append(d)

    recommendation_counts = Counter(d.recommendation_class for d in all_decisions)
    majority_counts: Counter = Counter()
    disagreement_items = 0
    model_alignment_checks = 0
    model_alignment_hits = 0

    for item_id, decisions in by_item.items():
        recs = [d.recommendation_class for d in decisions]
        if len(set(recs)) > 1:
            disagreement_items += 1
        majority = Counter(recs).most_common(1)[0][0]
        majority_counts[majority] += 1

        model_recs = {d.model_recommendation for d in decisions if d.model_recommendation}
        if model_recs:
            model_alignment_checks += 1
            if majority in model_recs:
                model_alignment_hits += 1

    total_items = len(by_item)
    total_decisions = len(all_decisions)
    total_errors = sum(s.errors for s in agent_states)

    # Fallback metrics
    fallback_count = sum(1 for d in all_decisions if d.is_fallback)
    non_fallback_count = total_decisions - fallback_count
    fallback_rate = round(fallback_count / total_decisions, 4) if total_decisions else 0.0
    non_fallback_rate = round(non_fallback_count / total_decisions, 4) if total_decisions else 0.0
    fallback_reason_distribution = dict(
        Counter(d.fallback_reason for d in all_decisions if d.fallback_reason)
    )
    abstain_count = recommendation_counts.get("abstain", 0)
    abstain_rate = round(abstain_count / total_decisions, 4) if total_decisions else 0.0
    class_coverage_count = len(recommendation_counts)

    # Model alignment coverage: fraction of items with a non-null model recommendation
    items_with_model = sum(
        1 for item_id, decs in by_item.items()
        if any(d.model_recommendation for d in decs)
    )
    model_alignment_coverage = (
        round(items_with_model / total_items, 4) if total_items else 0.0
    )

    return {
        "runName": run_name,
        "cycleId": cycle["id"],
        "cycleName": cycle["cycle_name"],
        "totalItems": total_items,
        "totalDecisions": total_decisions,
        "totalErrors": total_errors,
        "disagreementRate": round(disagreement_items / total_items, 4) if total_items else 0,
        "majorityRecommendationDistribution": dict(majority_counts),
        "recommendationDistribution": dict(recommendation_counts),
        "perAgentDistribution": per_agent_dist,
        "modelMajorityAlignmentRate": (
            round(model_alignment_hits / model_alignment_checks, 4)
            if model_alignment_checks
            else None
        ),
        "fallbackRate": fallback_rate,
        "nonFallbackRate": non_fallback_rate,
        "nonFallbackDecisionCount": non_fallback_count,
        "fallbackReasonDistribution": fallback_reason_distribution,
        "abstainRate": abstain_rate,
        "classCoverageCount": class_coverage_count,
        "modelAlignmentCoverage": model_alignment_coverage,
        "executionMode": "parallel_async_agents",
        "agentCount": len(agent_states),
        "generatedAt": datetime.now(UTC).isoformat(),
    }


def _validate_run_quality(
    summary: dict[str, Any],
    *,
    min_class_coverage: int,
    max_abstain_rate: float,
    min_non_fallback_rate: float,
) -> list[str]:
    failures: list[str] = []
    class_count = int(summary.get("classCoverageCount") or 0)
    abstain_rate = float(summary.get("abstainRate") or 0.0)
    non_fallback_rate = float(summary.get("nonFallbackRate") or 0.0)

    if class_count < min_class_coverage:
        failures.append(
            f"class_coverage {class_count} < required {min_class_coverage}"
        )
    if abstain_rate > max_abstain_rate:
        failures.append(
            f"abstain_rate {abstain_rate:.2%} > allowed {max_abstain_rate:.2%}"
        )
    if non_fallback_rate < min_non_fallback_rate:
        failures.append(
            f"non_fallback_rate {non_fallback_rate:.2%} < required {min_non_fallback_rate:.2%}"
        )
    return failures


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------


def _write_report(summary: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    slug = summary["runName"].replace(" ", "_").lower()
    json_path = output_dir / f"{slug}.json"
    md_path = output_dir / f"{slug}.md"

    json_path.write_text(json.dumps(summary, indent=2, default=_json_default), encoding="utf-8")

    md_lines = [
        f"# Multi-Agent Synthetic Analyst Pilot — {summary['runName']}",
        "",
        f"- Execution mode: **{summary['executionMode']}**",
        f"- Agent count: {summary['agentCount']}",
        f"- Cycle: `{summary['cycleName']}` ({summary['cycleId']})",
        f"- Deals evaluated: {summary['totalItems']}",
        f"- Decisions generated: {summary['totalDecisions']} "
        f"({summary['agentCount']} agents x {summary['totalItems']} deals)",
        f"- Errors: {summary['totalErrors']}",
        f"- Disagreement rate: {summary['disagreementRate']}",
        f"- Model-majority alignment rate: {summary['modelMajorityAlignmentRate']}",
        f"- Fallback rate: {summary.get('fallbackRate', 0)}",
        f"- Abstain rate: {summary.get('abstainRate', 0)}",
        f"- Class coverage count: {summary.get('classCoverageCount', 0)}",
        f"- Quality gates passed: {summary.get('qualityGatePassed', True)}",
        "",
        "## Majority Recommendation Distribution",
        "",
    ]
    for k, v in summary["majorityRecommendationDistribution"].items():
        md_lines.append(f"- {k}: {v}")

    md_lines.extend(["", "## All Recommendation Distribution", ""])
    for k, v in summary["recommendationDistribution"].items():
        md_lines.append(f"- {k}: {v}")

    md_lines.extend(["", "## Per-Agent Distribution", ""])
    for agent_id, dist in summary["perAgentDistribution"].items():
        md_lines.append(f"### {agent_id}")
        for k, v in dist.items():
            md_lines.append(f"- {k}: {v}")
        md_lines.append("")

    fallback_reasons = summary.get("fallbackReasonDistribution", {})
    if fallback_reasons:
        md_lines.extend(["## Fallback Reasons", ""])
        for reason, count in fallback_reasons.items():
            md_lines.append(f"- {reason}: {count}")
        md_lines.append("")

    quality_failures = summary.get("qualityGateFailures", [])
    if quality_failures:
        md_lines.extend(["## Quality Gate Failures", ""])
        for failure in quality_failures:
            md_lines.append(f"- {failure}")
        md_lines.append("")

    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    return json_path, md_path


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


@app.command()
def main(
    cycle_id: str | None = typer.Option(
        None, help="Shadow cycle id (defaults to latest active cycle)."
    ),
    max_items: int = typer.Option(12, help="Max shadow-cycle deals to evaluate."),
    run_name: str | None = typer.Option(None, help="Custom run name."),
    output_dir: str = typer.Option("reports/pilots", help="Directory for pilot reports."),
    item_ids_file: str | None = typer.Option(
        None, help="Path to file with one shadow_cycle_item_id per line (locks item set)."
    ),
    strict_no_fallback: bool = typer.Option(
        False, help="Disable abstain-to-fallback mapping; keep abstain as-is."
    ),
    temperature: float = typer.Option(0.2, help="LLM sampling temperature."),
    min_sufficiency_score: float = typer.Option(
        DEFAULT_MIN_SUFFICIENCY_SCORE,
        help="Minimum data sufficiency score (0-100) required for pilot items.",
    ),
    min_category_count: int = typer.Option(
        DEFAULT_MIN_CATEGORY_COUNT,
        help="Minimum populated evidence categories required per item.",
    ),
    min_model_coverage: float = typer.Option(
        DEFAULT_MIN_MODEL_COVERAGE,
        help="Minimum fraction of selected items that must have model recommendations.",
    ),
    min_class_coverage: int = typer.Option(
        DEFAULT_MIN_CLASS_COVERAGE,
        help="Minimum distinct recommendation classes in selected pilot set.",
    ),
    max_abstain_rate: float = typer.Option(
        0.60,
        help="Maximum allowed abstain share in final agent decisions.",
    ),
    min_non_fallback_rate: float = typer.Option(
        0.70,
        help="Minimum required non-fallback decision rate in final agent decisions.",
    ),
    seed_model_evals: bool = typer.Option(
        True,
        "--seed-model-evals/--no-seed-model-evals",
        help="Seed deterministic model-linked evaluations when coverage is below threshold.",
    ),
) -> None:
    if min_category_count < 1:
        raise typer.BadParameter("min_category_count must be >= 1")
    if not (0.0 <= min_model_coverage <= 1.0):
        raise typer.BadParameter("min_model_coverage must be in [0, 1]")
    if min_class_coverage < 1:
        raise typer.BadParameter("min_class_coverage must be >= 1")
    if not (0.0 <= min_sufficiency_score <= 100.0):
        raise typer.BadParameter("min_sufficiency_score must be in [0, 100]")
    if not (0.0 <= max_abstain_rate <= 1.0):
        raise typer.BadParameter("max_abstain_rate must be in [0, 1]")
    if not (0.0 <= min_non_fallback_rate <= 1.0):
        raise typer.BadParameter("min_non_fallback_rate must be in [0, 1]")

    settings = get_settings()
    if not settings.anthropic_api_key:
        raise typer.BadParameter("SL_ANTHROPIC_API_KEY is required for multi-agent pilot.")

    conn = get_connection(settings)

    try:
        cycle = _load_cycle(conn, cycle_id)

        if item_ids_file:
            ids_path = Path(item_ids_file)
            if not ids_path.exists():
                raise typer.BadParameter(f"Item IDs file not found: {ids_path}")
            locked_ids = [
                line.strip() for line in ids_path.read_text().splitlines() if line.strip()
            ]
            if not locked_ids:
                raise typer.BadParameter("Item IDs file is empty.")
            items = _load_cycle_items_by_ids(conn, cycle["id"], locked_ids)
            if len(items) != len(locked_ids):
                missing = set(locked_ids) - {i["shadow_cycle_item_id"] for i in items}
                raise typer.BadParameter(
                    f"Missing {len(missing)} item(s) in cycle: {missing}"
                )
        else:
            items = _load_cycle_items(conn, cycle["id"], max_items)

        if not items:
            raise typer.BadParameter("No shadow-cycle items found for pilot run.")

        eligible_items = [
            item
            for item in items
            if (item.get("data_sufficiency", {}).get("score") or 0) >= min_sufficiency_score
            and (item.get("data_sufficiency", {}).get("category_count") or 0) >= min_category_count
        ]
        if len(eligible_items) < max(min_class_coverage, 3):
            missing = _missing_field_histogram(items)
            missing_str = ", ".join(f"{k}:{v}" for k, v in missing) if missing else "n/a"
            raise typer.BadParameter(
                "Insufficient pilot-ready items after data-sufficiency filter. "
                f"eligible={len(eligible_items)} of {len(items)}; "
                f"thresholds(score>={min_sufficiency_score}, categories>={min_category_count}); "
                f"top_missing={missing_str}"
            )

        seeded_count = 0
        if seed_model_evals:
            seeded_count = _seed_missing_model_evaluations(
                conn,
                eligible_items,
                min_model_coverage=min_model_coverage,
            )

        selected_items = _select_pilot_items(
            eligible_items,
            max_items=max_items,
            min_class_coverage=min_class_coverage,
            min_model_coverage=min_model_coverage,
        )
        if not selected_items:
            raise typer.BadParameter("No items selected after pilot curation.")

        class_count = len({_inferred_recommendation_class(i) for i in selected_items})
        if class_count < min_class_coverage:
            raise typer.BadParameter(
                f"Pilot class coverage below threshold: {class_count} < {min_class_coverage}"
            )
        model_coverage = (
            sum(1 for i in selected_items if i.get("model_recommendation"))
            / len(selected_items)
        )
        if model_coverage < min_model_coverage:
            raise typer.BadParameter(
                "Pilot model coverage below threshold: "
                f"{model_coverage:.2%} < {min_model_coverage:.2%}"
            )
        items = selected_items

        name = (
            run_name
            or f"agents_pilot_{_quarter_label(date.today())}_{cycle['cycle_name']}"
        )

        # Create run record
        run_row = execute_query(
            conn,
            """
            INSERT INTO synthetic_pilot_runs (
              run_name, cycle_id, model_name, max_items, started_at, notes
            )
            VALUES (%s, %s::uuid, %s, %s, now(), %s)
            RETURNING id::text
            """,
            (
                name,
                cycle["id"],
                MODEL,
                len(items),
                (
                    "Multi-agent parallel pilot "
                    f"(conservative/balanced/aggressive, suff>={min_sufficiency_score}, "
                    f"cats>={min_category_count}, class_cov>={min_class_coverage}, "
                    f"model_cov>={min_model_coverage:.2f}, seeded={seeded_count})"
                ),
            ),
        )
        run_id = run_row[0]["id"]

        logger.info(
            "multi_agent_pilot_starting",
            run_id=run_id,
            run_name=name,
            cycle_id=cycle["id"],
            cycle_name=cycle["cycle_name"],
            item_count=len(items),
            seeded_evaluations=seeded_count,
            model_coverage=model_coverage,
            class_coverage=class_count,
        )

        # Fan out 3 agents in parallel
        agent_states = asyncio.run(
            _fan_out_agents(
                settings.anthropic_api_key,
                items,
                strict_no_fallback=strict_no_fallback,
                temperature=temperature,
            )
        )

        # Persist all decisions (idempotent upsert)
        for state in agent_states:
            for decision in state.decisions:
                _upsert_decision(conn, run_id, decision)

        # Aggregate + report
        summary = _aggregate(name, cycle, agent_states)
        quality_failures = _validate_run_quality(
            summary,
            min_class_coverage=min_class_coverage,
            max_abstain_rate=max_abstain_rate,
            min_non_fallback_rate=min_non_fallback_rate,
        )
        summary["qualityGateFailures"] = quality_failures
        summary["qualityGatePassed"] = len(quality_failures) == 0

        json_path, md_path = _write_report(summary, Path(output_dir))

        # Update run with summary
        execute_query(
            conn,
            """
            UPDATE synthetic_pilot_runs
            SET completed_at = clock_timestamp(), summary = %s::jsonb
            WHERE id = %s::uuid
            """,
            (json.dumps(summary, default=_json_default), run_id),
        )
        conn.commit()

        if quality_failures:
            raise typer.BadParameter(
                "Pilot run failed quality gates: " + "; ".join(quality_failures)
            )

        logger.info(
            "multi_agent_pilot_completed",
            run_id=run_id,
            run_name=name,
            cycle_id=cycle["id"],
            items=len(items),
            decisions=summary["totalDecisions"],
            errors=summary["totalErrors"],
            disagreement_rate=summary["disagreementRate"],
            model_alignment_rate=summary["modelMajorityAlignmentRate"],
            fallback_rate=summary["fallbackRate"],
            non_fallback_rate=summary["nonFallbackRate"],
            model_alignment_coverage=summary["modelAlignmentCoverage"],
            report_json=str(json_path),
            report_md=str(md_path),
        )
    finally:
        conn.close()


if __name__ == "__main__":
    app()
