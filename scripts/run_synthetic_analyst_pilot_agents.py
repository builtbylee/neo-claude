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


def _has_actionable_context(context: dict[str, Any]) -> bool:
    company = context.get("company", {})
    model_output = context.get("model_output", {})
    deal = context.get("deal_snapshot", {})
    latest_round = deal.get("latest_round", {})

    if model_output.get("score") is not None:
        return True
    numeric_fields = [
        deal.get("funding_target"),
        deal.get("amount_raised"),
        deal.get("pre_money_valuation"),
        latest_round.get("amount_raised"),
        latest_round.get("pre_money_valuation"),
    ]
    has_numeric = any(v not in (None, 0, 0.0, "") for v in numeric_fields)
    has_categorical = bool(company.get("sector")) or bool(deal.get("stage_bucket"))
    return has_numeric or has_categorical


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
        "deal_snapshot": {
            "platform": item.get("cf_platform"),
            "campaign_date": item.get("campaign_date"),
            "funding_target": item.get("funding_target"),
            "amount_raised": item.get("amount_raised"),
            "pre_money_valuation": item.get("pre_money_valuation"),
            "equity_offered": item.get("equity_offered"),
            "investor_count": item.get("cf_investor_count"),
            "had_revenue": item.get("had_revenue"),
            "revenue_at_raise": item.get("revenue_at_raise"),
            "stage_bucket": item.get("stage_bucket"),
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


def _load_cycle_items(conn, cycle_id: str, max_items: int) -> list[dict[str, Any]]:
    return execute_query(
        conn,
        """
        WITH latest_eval AS (
          SELECT DISTINCT ON (company_name)
            id::text,
            company_name,
            recommendation_class,
            quantitative_score,
            confidence_level,
            category_scores,
            risk_flags,
            missing_data_fields,
            valuation_analysis,
            return_distribution,
            created_at
          FROM evaluations
          ORDER BY company_name, created_at DESC
        ),
        latest_cf AS (
          SELECT DISTINCT ON (lower(c.name))
            lower(c.name) AS name_key,
            co.platform AS cf_platform,
            co.campaign_date,
            co.funding_target,
            co.amount_raised,
            co.pre_money_valuation,
            co.equity_offered,
            co.investor_count AS cf_investor_count,
            co.had_revenue,
            co.revenue_at_raise,
            co.stage_bucket
          FROM crowdfunding_outcomes co
          JOIN companies c ON c.id = co.company_id
          ORDER BY lower(c.name), co.campaign_date DESC NULLS LAST
        ),
        latest_round AS (
          SELECT DISTINCT ON (lower(c.name))
            lower(c.name) AS name_key,
            fr.round_date,
            fr.round_type,
            fr.instrument_type,
            fr.amount_raised AS round_amount_raised,
            fr.pre_money_valuation AS round_pre_money_valuation,
            fr.investor_count AS round_investor_count,
            fr.qualified_institutional,
            fr.eis_seis_eligible,
            fr.qsbs_eligible
          FROM funding_rounds fr
          JOIN companies c ON c.id = fr.company_id
          ORDER BY lower(c.name), fr.round_date DESC NULLS LAST
        )
        SELECT
          sci.id::text AS shadow_cycle_item_id,
          sci.entity_id::text AS entity_id,
          sci.company_name,
          sci.sector,
          sci.country,
          sci.source,
          sci.source_ref,
          COALESCE(e.id::text, le.id) AS evaluation_id,
          COALESCE(e.recommendation_class, le.recommendation_class) AS model_recommendation,
          COALESCE(e.quantitative_score, le.quantitative_score) AS score,
          COALESCE(e.confidence_level, le.confidence_level) AS confidence_level,
          COALESCE(e.category_scores, le.category_scores) AS category_scores,
          COALESCE(e.risk_flags, le.risk_flags) AS risk_flags,
          COALESCE(e.missing_data_fields, le.missing_data_fields) AS missing_data_fields,
          COALESCE(e.valuation_analysis, le.valuation_analysis) AS valuation_analysis,
          COALESCE(e.return_distribution, le.return_distribution) AS return_distribution,
          lcf.cf_platform,
          lcf.campaign_date,
          lcf.funding_target,
          lcf.amount_raised,
          lcf.pre_money_valuation,
          lcf.equity_offered,
          lcf.cf_investor_count,
          lcf.had_revenue,
          lcf.revenue_at_raise,
          lcf.stage_bucket,
          lr.round_date,
          lr.round_type,
          lr.instrument_type,
          lr.round_amount_raised,
          lr.round_pre_money_valuation,
          lr.round_investor_count,
          lr.qualified_institutional,
          lr.eis_seis_eligible,
          lr.qsbs_eligible
        FROM shadow_cycle_items sci
        LEFT JOIN evaluations e ON e.id = sci.evaluation_id
        LEFT JOIN latest_eval le ON lower(le.company_name) = lower(sci.company_name)
        LEFT JOIN latest_cf lcf ON lower(sci.company_name) = lcf.name_key
        LEFT JOIN latest_round lr ON lower(sci.company_name) = lr.name_key
        WHERE sci.cycle_id = %s::uuid
        ORDER BY (COALESCE(e.id::text, le.id) IS NULL) ASC, sci.created_at ASC
        LIMIT %s
        """,
        (cycle_id, max_items),
    )


def _load_cycle_items_by_ids(
    conn, cycle_id: str, item_ids: list[str]
) -> list[dict[str, Any]]:
    """Load specific shadow-cycle items by ID, preserving the input order."""
    if not item_ids:
        return []
    placeholders = ", ".join(["%s::uuid"] * len(item_ids))
    rows = execute_query(
        conn,
        f"""
        WITH latest_eval AS (
          SELECT DISTINCT ON (company_name)
            id::text,
            company_name,
            recommendation_class,
            quantitative_score,
            confidence_level,
            category_scores,
            risk_flags,
            missing_data_fields,
            valuation_analysis,
            return_distribution,
            created_at
          FROM evaluations
          ORDER BY company_name, created_at DESC
        ),
        latest_cf AS (
          SELECT DISTINCT ON (lower(c.name))
            lower(c.name) AS name_key,
            co.platform AS cf_platform,
            co.campaign_date,
            co.funding_target,
            co.amount_raised,
            co.pre_money_valuation,
            co.equity_offered,
            co.investor_count AS cf_investor_count,
            co.had_revenue,
            co.revenue_at_raise,
            co.stage_bucket
          FROM crowdfunding_outcomes co
          JOIN companies c ON c.id = co.company_id
          ORDER BY lower(c.name), co.campaign_date DESC NULLS LAST
        ),
        latest_round AS (
          SELECT DISTINCT ON (lower(c.name))
            lower(c.name) AS name_key,
            fr.round_date,
            fr.round_type,
            fr.instrument_type,
            fr.amount_raised AS round_amount_raised,
            fr.pre_money_valuation AS round_pre_money_valuation,
            fr.investor_count AS round_investor_count,
            fr.qualified_institutional,
            fr.eis_seis_eligible,
            fr.qsbs_eligible
          FROM funding_rounds fr
          JOIN companies c ON c.id = fr.company_id
          ORDER BY lower(c.name), fr.round_date DESC NULLS LAST
        )
        SELECT
          sci.id::text AS shadow_cycle_item_id,
          sci.entity_id::text AS entity_id,
          sci.company_name,
          sci.sector,
          sci.country,
          sci.source,
          sci.source_ref,
          COALESCE(e.id::text, le.id) AS evaluation_id,
          COALESCE(e.recommendation_class, le.recommendation_class) AS model_recommendation,
          COALESCE(e.quantitative_score, le.quantitative_score) AS score,
          COALESCE(e.confidence_level, le.confidence_level) AS confidence_level,
          COALESCE(e.category_scores, le.category_scores) AS category_scores,
          COALESCE(e.risk_flags, le.risk_flags) AS risk_flags,
          COALESCE(e.missing_data_fields, le.missing_data_fields) AS missing_data_fields,
          COALESCE(e.valuation_analysis, le.valuation_analysis) AS valuation_analysis,
          COALESCE(e.return_distribution, le.return_distribution) AS return_distribution,
          lcf.cf_platform,
          lcf.campaign_date,
          lcf.funding_target,
          lcf.amount_raised,
          lcf.pre_money_valuation,
          lcf.equity_offered,
          lcf.cf_investor_count,
          lcf.had_revenue,
          lcf.revenue_at_raise,
          lcf.stage_bucket,
          lr.round_date,
          lr.round_type,
          lr.instrument_type,
          lr.round_amount_raised,
          lr.round_pre_money_valuation,
          lr.round_investor_count,
          lr.qualified_institutional,
          lr.eis_seis_eligible,
          lr.qsbs_eligible
        FROM shadow_cycle_items sci
        LEFT JOIN evaluations e ON e.id = sci.evaluation_id
        LEFT JOIN latest_eval le ON lower(le.company_name) = lower(sci.company_name)
        LEFT JOIN latest_cf lcf ON lower(sci.company_name) = lcf.name_key
        LEFT JOIN latest_round lr ON lower(sci.company_name) = lr.name_key
        WHERE sci.cycle_id = %s::uuid
          AND sci.id IN ({placeholders})
        ORDER BY sci.created_at ASC
        """,
        (cycle_id, *item_ids),
    )
    # Reorder to match input file order for full determinism
    by_id = {r["shadow_cycle_item_id"]: r for r in rows}
    return [by_id[iid] for iid in item_ids if iid in by_id]


def _upsert_decision(conn, run_id: str, decision: AgentDecision) -> None:
    """Idempotent upsert on (run_id, shadow_cycle_item_id, analyst_profile)."""
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
            json.dumps(decision.raw_response, default=_json_default),
        ),
    )


# ---------------------------------------------------------------------------
# Async agent worker
# ---------------------------------------------------------------------------


async def _ask_persona_async(
    client: anthropic.AsyncAnthropic,
    persona: dict[str, str],
    context: dict[str, Any],
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
                temperature=0.2,
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
    if rec not in RECOMMENDATIONS:
        rec = "abstain"

    conviction_raw = parsed.get("conviction", 0)
    try:
        conviction = int(conviction_raw)
    except (TypeError, ValueError):
        conviction = 0
    conviction = max(0, min(100, conviction))

    if rec == "abstain" and _has_actionable_context(context):
        fallback_rec, fallback_conviction = _persona_fallback_from_context(
            persona["id"], context
        )
        rec = fallback_rec
        if conviction == 0:
            conviction = fallback_conviction
        parsed["fallback_from_abstain"] = True

    return {
        "recommendation_class": rec,
        "conviction": conviction,
        "rationale": str(parsed.get("rationale", "No rationale provided")).strip(),
        "key_risks": parsed.get("key_risks") or [],
        "data_gaps": parsed.get("data_gaps") or [],
        "raw": parsed,
    }


async def _run_agent(
    client: anthropic.AsyncAnthropic,
    agent_state: AgentState,
    items: list[dict[str, Any]],
) -> None:
    """Run a single agent across all deals.  Mutates only its own AgentState."""
    persona = agent_state.profile

    for item in items:
        context = _build_context(item)
        try:
            result = await _ask_persona_async(client, persona, context)
        except Exception as exc:
            logger.warning(
                "agent_persona_failed",
                company_name=item.get("company_name"),
                persona=persona["id"],
                error=str(exc),
            )
            result = {
                "recommendation_class": "abstain",
                "conviction": 0,
                "rationale": "Persona scoring failed; defaulted to abstain.",
                "key_risks": ["persona_scoring_error"],
                "data_gaps": ["claude_response_unavailable"],
                "raw": {"error": str(exc)},
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
            )
        )


async def _fan_out_agents(
    api_key: str,
    items: list[dict[str, Any]],
) -> list[AgentState]:
    """Launch 3 independent agent workers in parallel, return their states."""
    # Each agent gets its OWN async client instance and state object.
    agents: list[AgentState] = []
    tasks: list[asyncio.Task] = []

    for profile in AGENT_PROFILES:
        state = AgentState(profile=profile)
        agents.append(state)
        client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=2)
        tasks.append(asyncio.create_task(_run_agent(client, state, items)))

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
        "executionMode": "parallel_async_agents",
        "agentCount": len(agent_states),
        "generatedAt": datetime.now(UTC).isoformat(),
    }


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
    max_items: int = typer.Option(20, help="Max shadow-cycle deals to evaluate."),
    run_name: str | None = typer.Option(None, help="Custom run name."),
    output_dir: str = typer.Option("reports/pilots", help="Directory for pilot reports."),
    item_ids_file: str | None = typer.Option(
        None, help="Path to file with one shadow_cycle_item_id per line (locks item set)."
    ),
) -> None:
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
                max_items,
                "Multi-agent parallel pilot (conservative/balanced/aggressive)",
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
        )

        # Fan out 3 agents in parallel
        agent_states = asyncio.run(
            _fan_out_agents(settings.anthropic_api_key, items)
        )

        # Persist all decisions (idempotent upsert)
        for state in agent_states:
            for decision in state.decisions:
                _upsert_decision(conn, run_id, decision)

        # Aggregate + report
        summary = _aggregate(name, cycle, agent_states)
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
            report_json=str(json_path),
            report_md=str(md_path),
        )
    finally:
        conn.close()


if __name__ == "__main__":
    app()
