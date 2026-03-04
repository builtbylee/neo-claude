#!/usr/bin/env python3
"""Compute stability metrics across repeated multi-agent pilot runs.

Reads per-decision data from synthetic_pilot_decisions for a set of run IDs
and produces a stability report with pairwise agreement, drift, fallback-adjusted
metrics, and pass/fail gates.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from itertools import combinations
from pathlib import Path
from typing import Any

import structlog
import typer

from startuplens.config import get_settings
from startuplens.db import execute_query, get_connection

logger = structlog.get_logger(__name__)
app = typer.Typer()


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def _load_decisions(conn, run_ids: list[str]) -> list[dict[str, Any]]:
    """Load all decisions for the given run IDs, including fallback metadata."""
    placeholders = ", ".join(["%s::uuid"] * len(run_ids))
    return execute_query(
        conn,
        f"""
        SELECT
          d.run_id::text,
          r.run_name,
          d.shadow_cycle_item_id::text,
          sci.company_name,
          d.analyst_profile,
          d.recommendation_class,
          d.conviction::integer AS conviction,
          d.raw_response
        FROM synthetic_pilot_decisions d
        JOIN synthetic_pilot_runs r ON r.id = d.run_id
        JOIN shadow_cycle_items sci ON sci.id = d.shadow_cycle_item_id
        WHERE d.run_id IN ({placeholders})
        ORDER BY d.shadow_cycle_item_id, d.analyst_profile, r.started_at
        """,
        tuple(run_ids),
    )


def _is_fallback(d: dict[str, Any]) -> bool:
    """Determine if a decision was fallback-derived."""
    raw = d.get("raw_response") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            raw = {}
    return bool(raw.get("is_fallback") or raw.get("fallback_from_abstain"))


def _compute_stability(
    run_ids: list[str],
    run_names: list[str],
    decisions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute all stability metrics including fallback-adjusted versions."""
    agents = ["conservative", "balanced", "aggressive"]
    run_pairs = list(combinations(range(len(run_ids)), 2))
    pair_labels = [f"{run_names[a]} vs {run_names[b]}" for a, b in run_pairs]

    # Index: (item_id, agent, run_id) -> decision
    idx: dict[tuple[str, str, str], dict[str, Any]] = {}
    for d in decisions:
        key = (d["shadow_cycle_item_id"], d["analyst_profile"], d["run_id"])
        idx[key] = d

    item_ids = sorted({d["shadow_cycle_item_id"] for d in decisions})

    # ---- Fallback metrics ----
    total_decisions = len(decisions)
    fallback_count = sum(1 for d in decisions if _is_fallback(d))
    non_fallback_count = total_decisions - fallback_count
    fallback_rate = round(fallback_count / total_decisions, 4) if total_decisions else 0.0
    non_fallback_rate = (
        round(non_fallback_count / total_decisions, 4) if total_decisions else 0.0
    )

    # Per-agent fallback rate
    per_agent_fallback: dict[str, float] = {}
    for agent in agents:
        agent_decs = [d for d in decisions if d["analyst_profile"] == agent]
        agent_fb = sum(1 for d in agent_decs if _is_fallback(d))
        per_agent_fallback[agent] = (
            round(agent_fb / len(agent_decs), 4) if agent_decs else 0.0
        )

    # ---- 1. Per-agent pairwise agreement (all decisions) ----
    per_agent_pairwise: dict[str, dict[str, float]] = {}
    for agent in agents:
        agent_pairs: dict[str, float] = {}
        for pi, (ra, rb) in enumerate(run_pairs):
            agree = 0
            total = 0
            for iid in item_ids:
                da = idx.get((iid, agent, run_ids[ra]))
                db = idx.get((iid, agent, run_ids[rb]))
                if da and db:
                    total += 1
                    if da["recommendation_class"] == db["recommendation_class"]:
                        agree += 1
            agent_pairs[pair_labels[pi]] = round(agree / total, 4) if total else 0.0
        per_agent_pairwise[agent] = agent_pairs

    # ---- 1b. Non-fallback per-agent pairwise agreement ----
    nf_per_agent_pairwise: dict[str, dict[str, float]] = {}
    for agent in agents:
        agent_pairs: dict[str, float] = {}
        for pi, (ra, rb) in enumerate(run_pairs):
            agree = 0
            total = 0
            for iid in item_ids:
                da = idx.get((iid, agent, run_ids[ra]))
                db = idx.get((iid, agent, run_ids[rb]))
                if da and db and not _is_fallback(da) and not _is_fallback(db):
                    total += 1
                    if da["recommendation_class"] == db["recommendation_class"]:
                        agree += 1
            agent_pairs[pair_labels[pi]] = round(agree / total, 4) if total else 0.0
        nf_per_agent_pairwise[agent] = agent_pairs

    # ---- 2. Majority recommendation agreement across runs per deal ----
    def _majority_for_run(run_id: str, item_id: str) -> str | None:
        recs = []
        for agent in agents:
            d = idx.get((item_id, agent, run_id))
            if d:
                recs.append(d["recommendation_class"])
        if not recs:
            return None
        return Counter(recs).most_common(1)[0][0]

    majority_agreement_per_deal: dict[str, dict[str, Any]] = {}
    majority_agree_count = 0
    for iid in item_ids:
        majorities = [_majority_for_run(rid, iid) for rid in run_ids]
        company = next(
            (d["company_name"] for d in decisions if d["shadow_cycle_item_id"] == iid),
            iid,
        )
        all_same = len(set(m for m in majorities if m is not None)) == 1
        if all_same:
            majority_agree_count += 1
        majority_agreement_per_deal[company] = {
            "majorities": {run_names[i]: m for i, m in enumerate(majorities)},
            "stable": all_same,
        }

    majority_agreement_rate = (
        round(majority_agree_count / len(item_ids), 4) if item_ids else 0.0
    )

    # ---- 2b. Non-fallback majority agreement ----
    def _nf_majority_for_run(run_id: str, item_id: str) -> str | None:
        recs = []
        for agent in agents:
            d = idx.get((item_id, agent, run_id))
            if d and not _is_fallback(d):
                recs.append(d["recommendation_class"])
        if not recs:
            return None
        return Counter(recs).most_common(1)[0][0]

    nf_majority_agree = 0
    nf_majority_total = 0
    for iid in item_ids:
        nf_majorities = [_nf_majority_for_run(rid, iid) for rid in run_ids]
        non_none = [m for m in nf_majorities if m is not None]
        if len(non_none) >= 2:
            nf_majority_total += 1
            if len(set(non_none)) == 1:
                nf_majority_agree += 1

    nf_majority_agreement_rate = (
        round(nf_majority_agree / nf_majority_total, 4) if nf_majority_total else 0.0
    )

    # ---- 3. Recommendation drift count ----
    drift_count = len(item_ids) - majority_agree_count

    # ---- 4. Disagreement-rate variance across runs ----
    per_run_disagreement: dict[str, float] = {}
    for i, rid in enumerate(run_ids):
        disagree_items = 0
        for iid in item_ids:
            recs = set()
            for agent in agents:
                d = idx.get((iid, agent, rid))
                if d:
                    recs.add(d["recommendation_class"])
            if len(recs) > 1:
                disagree_items += 1
        per_run_disagreement[run_names[i]] = (
            round(disagree_items / len(item_ids), 4) if item_ids else 0.0
        )

    disagreement_values = list(per_run_disagreement.values())
    disagreement_stddev = (
        round(statistics.stdev(disagreement_values), 4)
        if len(disagreement_values) > 1
        else 0.0
    )

    # ---- 5. Class distribution drift ----
    all_classes = sorted({d["recommendation_class"] for d in decisions})
    per_run_class_dist: dict[str, dict[str, int]] = {}
    for i, rid in enumerate(run_ids):
        counts = Counter(
            d["recommendation_class"] for d in decisions if d["run_id"] == rid
        )
        per_run_class_dist[run_names[i]] = {c: counts.get(c, 0) for c in all_classes}

    class_drift_pairs: dict[str, dict[str, int]] = {}
    for pi, (ra, rb) in enumerate(run_pairs):
        deltas: dict[str, int] = {}
        for c in all_classes:
            deltas[c] = abs(
                per_run_class_dist[run_names[ra]].get(c, 0)
                - per_run_class_dist[run_names[rb]].get(c, 0)
            )
        class_drift_pairs[pair_labels[pi]] = deltas

    # ---- 6. Conviction drift (non-fallback only) ----
    nf_per_agent_conviction: dict[str, dict[str, list[int]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for d in decisions:
        if not _is_fallback(d):
            nf_per_agent_conviction[d["analyst_profile"]][d["run_name"]].append(
                d["conviction"]
            )

    conviction_drift: dict[str, dict[str, dict[str, float]]] = {}
    for agent in agents:
        agent_stats: dict[str, dict[str, float]] = {}
        for rn in run_names:
            vals = nf_per_agent_conviction[agent].get(rn, [])
            if vals:
                agent_stats[rn] = {
                    "mean": round(statistics.mean(vals), 2),
                    "median": round(statistics.median(vals), 2),
                    "stdev": round(statistics.stdev(vals), 2) if len(vals) > 1 else 0.0,
                }
            else:
                agent_stats[rn] = {"mean": 0.0, "median": 0.0, "stdev": 0.0}
        conviction_drift[agent] = agent_stats

    # ---- Model alignment coverage ----
    # Check run summaries for modelAlignmentCoverage; if not available, query DB
    # The decisions table doesn't store model_recommendation directly.
    # Use the run summary (stored in synthetic_pilot_runs.summary) or default to 0.
    model_alignment_coverage = 0.0

    # ---- Class coverage count ----
    class_coverage_count = len(all_classes)

    # ---- Gates (new G1-G6) ----
    # G1: fallback_rate <= 0.25
    g1_pass = fallback_rate <= 0.25
    # G2: non_fallback_majority_agreement >= 0.75
    g2_pass = nf_majority_agreement_rate >= 0.75
    # G3: non_fallback_agent_pairwise_agreement >= 0.80 (min across all)
    all_nf_pairwise = [
        v for av in nf_per_agent_pairwise.values() for v in av.values()
    ]
    g3_min = min(all_nf_pairwise) if all_nf_pairwise else 0.0
    g3_pass = g3_min >= 0.80
    # G4: disagreement_stddev <= 0.10
    g4_pass = disagreement_stddev <= 0.10
    # G5: model_alignment_coverage >= 0.30
    g5_pass = model_alignment_coverage >= 0.30
    # G6: class_coverage_count >= 3
    g6_pass = class_coverage_count >= 3

    overall_pass = all([g1_pass, g2_pass, g3_pass, g4_pass, g5_pass, g6_pass])

    gate_failures: list[str] = []
    if not g1_pass:
        gate_failures.append(
            f"G1 FAIL: fallback_rate {fallback_rate:.2%} > 25%"
        )
    if not g2_pass:
        gate_failures.append(
            f"G2 FAIL: non_fallback_majority_agreement "
            f"{nf_majority_agreement_rate:.2%} < 75%"
        )
    if not g3_pass:
        gate_failures.append(
            f"G3 FAIL: min non_fallback_agent_pairwise {g3_min:.2%} < 80%"
        )
    if not g4_pass:
        gate_failures.append(
            f"G4 FAIL: disagreement_stddev {disagreement_stddev:.4f} > 0.10"
        )
    if not g5_pass:
        gate_failures.append(
            f"G5 FAIL: model_alignment_coverage {model_alignment_coverage:.2%} < 30% "
            f"(0 evaluations in DB)"
        )
    if not g6_pass:
        gate_failures.append(
            f"G6 FAIL: class_coverage_count {class_coverage_count} < 3 "
            f"(observed: {', '.join(all_classes)})"
        )

    return {
        "analysisType": "repeat_stability_fallback_adjusted",
        "runIds": run_ids,
        "runNames": run_names,
        "itemCount": len(item_ids),
        "decisionCount": total_decisions,
        "fallbackRate": fallback_rate,
        "nonFallbackRate": non_fallback_rate,
        "nonFallbackDecisionCount": non_fallback_count,
        "perAgentFallbackRate": per_agent_fallback,
        "perAgentPairwiseAgreement": per_agent_pairwise,
        "nonFallbackPerAgentPairwiseAgreement": nf_per_agent_pairwise,
        "majorityAgreementRate": majority_agreement_rate,
        "nonFallbackMajorityAgreementRate": nf_majority_agreement_rate,
        "majorityAgreementPerDeal": majority_agreement_per_deal,
        "recommendationDriftCount": drift_count,
        "perRunDisagreementRate": per_run_disagreement,
        "disagreementRateStddev": disagreement_stddev,
        "perRunClassDistribution": per_run_class_dist,
        "classDistributionDriftPairwise": class_drift_pairs,
        "nonFallbackConvictionDriftByAgent": conviction_drift,
        "modelAlignmentCoverage": model_alignment_coverage,
        "classCoverageCount": class_coverage_count,
        "classesObserved": all_classes,
        "gates": {
            "G1_fallback_rate_le_25pct": {
                "value": fallback_rate,
                "threshold": 0.25,
                "pass": g1_pass,
            },
            "G2_nf_majority_agreement_ge_75pct": {
                "value": nf_majority_agreement_rate,
                "threshold": 0.75,
                "pass": g2_pass,
            },
            "G3_nf_agent_pairwise_ge_80pct": {
                "value": g3_min,
                "threshold": 0.80,
                "pass": g3_pass,
            },
            "G4_disagreement_stddev_le_010": {
                "value": disagreement_stddev,
                "threshold": 0.10,
                "pass": g4_pass,
            },
            "G5_model_alignment_coverage_ge_30pct": {
                "value": model_alignment_coverage,
                "threshold": 0.30,
                "pass": g5_pass,
            },
            "G6_class_coverage_ge_3": {
                "value": class_coverage_count,
                "threshold": 3,
                "pass": g6_pass,
            },
        },
        "overallPass": overall_pass,
        "gateFailures": gate_failures,
        "generatedAt": datetime.now(UTC).isoformat(),
    }


def _write_stability_report(
    result: dict[str, Any], output_dir: Path, report_name: str
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / f"{report_name}.json"
    md_path = output_dir / f"{report_name}.md"

    json_path.write_text(
        json.dumps(result, indent=2, default=_json_default), encoding="utf-8"
    )

    status = "PASS" if result["overallPass"] else "FAIL"
    md = [
        f"# Multi-Agent Pilot Stability Report — {report_name}",
        "",
        f"**Overall: {status}**",
        "",
        f"- Runs compared: {len(result['runIds'])}",
        f"- Items per run: {result['itemCount']}",
        f"- Total decisions: {result['decisionCount']}",
        f"- Fallback rate: {result['fallbackRate']:.2%}",
        f"- Non-fallback rate: {result['nonFallbackRate']:.2%}",
        f"- Classes observed: {', '.join(result['classesObserved'])}",
        "",
        "## Gates",
        "",
        "| Gate | Value | Threshold | Result |",
        "|------|-------|-----------|--------|",
    ]
    for gname, g in result["gates"].items():
        pf = "PASS" if g["pass"] else "**FAIL**"
        val = g["value"]
        val_str = f"{val:.4f}" if isinstance(val, float) else str(val)
        md.append(f"| {gname} | {val_str} | {g['threshold']} | {pf} |")

    if result["gateFailures"]:
        md.extend(["", "### Gate Failures", ""])
        for f in result["gateFailures"]:
            md.append(f"- {f}")

    md.extend(["", "## Per-Agent Fallback Rate", ""])
    for agent, rate in result["perAgentFallbackRate"].items():
        md.append(f"- {agent}: {rate:.2%}")

    md.extend(["", "## Non-Fallback Per-Agent Pairwise Agreement", ""])
    md.append("| Agent | Pair | Agreement |")
    md.append("|-------|------|-----------|")
    for agent, pairs in result["nonFallbackPerAgentPairwiseAgreement"].items():
        for pair, val in pairs.items():
            md.append(f"| {agent} | {pair} | {val:.2%} |")

    md.extend(["", "## Majority Agreement Per Deal", ""])
    run_names = result["runNames"]
    header_cols = " | ".join(rn for rn in run_names)
    md.append(f"| Company | Stable | {header_cols} |")
    sep_cols = " | ".join(["---"] * len(run_names))
    md.append(f"|---------|--------|{sep_cols}|")
    for company, info in result["majorityAgreementPerDeal"].items():
        stable = "Yes" if info["stable"] else "**No**"
        majorities = info["majorities"]
        cols = " | ".join(str(majorities.get(rn, "")) for rn in run_names)
        md.append(f"| {company} | {stable} | {cols} |")

    md.extend([
        "",
        f"- Majority agreement rate: **{result['majorityAgreementRate']:.2%}**",
        f"- Non-fallback majority agreement: "
        f"**{result['nonFallbackMajorityAgreementRate']:.2%}**",
        f"- Recommendation drift count: **{result['recommendationDriftCount']}**",
    ])

    md.extend(["", "## Disagreement Rate Across Runs", ""])
    for rn, dr in result["perRunDisagreementRate"].items():
        md.append(f"- {rn}: {dr}")
    md.append(f"- Stddev: {result['disagreementRateStddev']}")

    md.extend(["", "## Class Distribution Per Run", ""])
    first_dist = result["perRunClassDistribution"].get(run_names[0], {})
    cols_header = " | ".join(sorted(first_dist.keys()))
    md.append(f"| Run | {cols_header} |")
    md.append("|-----|" + "|".join(["---"] * len(first_dist)) + "|")
    for rn, dist in result["perRunClassDistribution"].items():
        cols = " | ".join(str(dist[c]) for c in sorted(dist.keys()))
        md.append(f"| {rn} | {cols} |")

    md.extend(["", "## Non-Fallback Conviction Drift by Agent", ""])
    for agent, runs in result["nonFallbackConvictionDriftByAgent"].items():
        md.append(f"### {agent}")
        for rn, stats in runs.items():
            md.append(
                f"- {rn}: mean={stats['mean']}, median={stats['median']}, "
                f"stdev={stats['stdev']}"
            )
        md.append("")

    md_path.write_text("\n".join(md), encoding="utf-8")
    return json_path, md_path


@app.command()
def main(
    run_ids: list[str] = typer.Argument(
        ..., help="Space-separated run IDs to compare."
    ),
    output_dir: str = typer.Option(
        "reports/pilots/repeats", help="Directory for stability report."
    ),
    report_name: str = typer.Option(
        "stability_report", help="Base name for output files (without extension)."
    ),
) -> None:
    settings = get_settings()
    conn = get_connection(settings)

    try:
        decisions = _load_decisions(conn, run_ids)
        if not decisions:
            raise typer.BadParameter("No decisions found for the given run IDs.")

        run_names = []
        seen: set[str] = set()
        for d in decisions:
            if d["run_id"] not in seen:
                run_names.append(d["run_name"])
                seen.add(d["run_id"])

        result = _compute_stability(run_ids, run_names, decisions)
        json_path, md_path = _write_stability_report(
            result, Path(output_dir), report_name
        )

        logger.info(
            "stability_analysis_completed",
            overall_pass=result["overallPass"],
            fallback_rate=result["fallbackRate"],
            nf_majority_agreement=result["nonFallbackMajorityAgreementRate"],
            majority_agreement=result["majorityAgreementRate"],
            drift_count=result["recommendationDriftCount"],
            disagreement_stddev=result["disagreementRateStddev"],
            model_alignment_coverage=result["modelAlignmentCoverage"],
            class_coverage=result["classCoverageCount"],
            gate_failures=len(result["gateFailures"]),
            report_json=str(json_path),
            report_md=str(md_path),
        )
    finally:
        conn.close()


if __name__ == "__main__":
    app()
