#!/usr/bin/env python3
"""Compute stability metrics across repeated multi-agent pilot runs.

Reads per-decision data from synthetic_pilot_decisions for a set of run IDs
and produces a stability report with pairwise agreement, drift, and gate
pass/fail.
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
    """Load all decisions for the given run IDs."""
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
          d.conviction::integer AS conviction
        FROM synthetic_pilot_decisions d
        JOIN synthetic_pilot_runs r ON r.id = d.run_id
        JOIN shadow_cycle_items sci ON sci.id = d.shadow_cycle_item_id
        WHERE d.run_id IN ({placeholders})
        ORDER BY d.shadow_cycle_item_id, d.analyst_profile, r.started_at
        """,
        tuple(run_ids),
    )


def _compute_stability(
    run_ids: list[str],
    run_names: list[str],
    decisions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute all stability metrics."""
    agents = ["conservative", "balanced", "aggressive"]
    run_pairs = list(combinations(range(len(run_ids)), 2))
    pair_labels = [f"{run_names[a]} vs {run_names[b]}" for a, b in run_pairs]

    # Index: (item_id, agent, run_id) -> decision
    idx: dict[tuple[str, str, str], dict[str, Any]] = {}
    for d in decisions:
        key = (d["shadow_cycle_item_id"], d["analyst_profile"], d["run_id"])
        idx[key] = d

    item_ids = sorted({d["shadow_cycle_item_id"] for d in decisions})

    # ---- 1. Per-agent pairwise agreement ----
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

    majority_agreement_per_deal: dict[str, dict[str, str | bool]] = {}
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

    # ---- 5. Class distribution drift (absolute delta by class, run-to-run) ----
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

    # ---- 6. Confidence drift summary ----
    per_agent_conviction: dict[str, dict[str, list[int]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for d in decisions:
        per_agent_conviction[d["analyst_profile"]][d["run_name"]].append(d["conviction"])

    conviction_drift: dict[str, dict[str, dict[str, float]]] = {}
    for agent in agents:
        agent_stats: dict[str, dict[str, float]] = {}
        for rn in run_names:
            vals = per_agent_conviction[agent].get(rn, [])
            if vals:
                agent_stats[rn] = {
                    "mean": round(statistics.mean(vals), 2),
                    "median": round(statistics.median(vals), 2),
                }
            else:
                agent_stats[rn] = {"mean": 0.0, "median": 0.0}
        conviction_drift[agent] = agent_stats

    # ---- Gates ----
    # G1: majority agreement >= 80%
    g1_pass = majority_agreement_rate >= 0.80
    # G2: per-agent pairwise agreement >= 85% (all pairs, all agents)
    all_pairwise = [
        v for agent_vals in per_agent_pairwise.values() for v in agent_vals.values()
    ]
    g2_min = min(all_pairwise) if all_pairwise else 0.0
    g2_pass = g2_min >= 0.85
    # G3: disagreement-rate stddev <= 0.10
    g3_pass = disagreement_stddev <= 0.10

    overall_pass = g1_pass and g2_pass and g3_pass

    gate_failures: list[str] = []
    if not g1_pass:
        gate_failures.append(
            f"G1 FAIL: majority agreement {majority_agreement_rate:.2%} < 80%"
        )
    if not g2_pass:
        gate_failures.append(
            f"G2 FAIL: min per-agent pairwise agreement {g2_min:.2%} < 85%"
        )
    if not g3_pass:
        gate_failures.append(
            f"G3 FAIL: disagreement-rate stddev {disagreement_stddev:.4f} > 0.10"
        )

    return {
        "analysisType": "repeat_stability",
        "runIds": run_ids,
        "runNames": run_names,
        "itemCount": len(item_ids),
        "decisionCount": len(decisions),
        "perAgentPairwiseAgreement": per_agent_pairwise,
        "majorityAgreementRate": majority_agreement_rate,
        "majorityAgreementPerDeal": majority_agreement_per_deal,
        "recommendationDriftCount": drift_count,
        "perRunDisagreementRate": per_run_disagreement,
        "disagreementRateStddev": disagreement_stddev,
        "perRunClassDistribution": per_run_class_dist,
        "classDistributionDriftPairwise": class_drift_pairs,
        "convictionDriftByAgent": conviction_drift,
        "gates": {
            "G1_majority_agreement_ge_80pct": {
                "value": majority_agreement_rate,
                "threshold": 0.80,
                "pass": g1_pass,
            },
            "G2_per_agent_pairwise_ge_85pct": {
                "value": g2_min,
                "threshold": 0.85,
                "pass": g2_pass,
            },
            "G3_disagreement_stddev_le_010": {
                "value": disagreement_stddev,
                "threshold": 0.10,
                "pass": g3_pass,
            },
        },
        "overallPass": overall_pass,
        "gateFailures": gate_failures,
        "generatedAt": datetime.now(UTC).isoformat(),
    }


def _write_stability_report(
    result: dict[str, Any], output_dir: Path
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / "agents_repeat_stability_2026q1.json"
    md_path = output_dir / "agents_repeat_stability_2026q1.md"

    json_path.write_text(
        json.dumps(result, indent=2, default=_json_default), encoding="utf-8"
    )

    status = "PASS" if result["overallPass"] else "FAIL"
    md = [
        "# Multi-Agent Pilot Stability Report — 2026-Q1",
        "",
        f"**Overall: {status}**",
        "",
        f"- Runs compared: {len(result['runIds'])}",
        f"- Items per run: {result['itemCount']}",
        f"- Total decisions: {result['decisionCount']}",
        "",
        "## Gates",
        "",
        "| Gate | Metric | Value | Threshold | Result |",
        "|------|--------|-------|-----------|--------|",
    ]
    for gname, g in result["gates"].items():
        pf = "PASS" if g["pass"] else "FAIL"
        md.append(f"| {gname} | | {g['value']:.4f} | {g['threshold']} | {pf} |")

    if result["gateFailures"]:
        md.extend(["", "### Failures", ""])
        for f in result["gateFailures"]:
            md.append(f"- {f}")

    md.extend(["", "## Per-Agent Pairwise Recommendation Agreement", ""])
    md.append("| Agent | Pair | Agreement |")
    md.append("|-------|------|-----------|")
    for agent, pairs in result["perAgentPairwiseAgreement"].items():
        for pair, val in pairs.items():
            md.append(f"| {agent} | {pair} | {val:.2%} |")

    md.extend(["", "## Majority Agreement Per Deal", ""])
    md.append("| Company | Stable | R1 | R2 | R3 |")
    md.append("|---------|--------|----|----|-----|")
    run_names = result["runNames"]
    for company, info in result["majorityAgreementPerDeal"].items():
        stable = "Yes" if info["stable"] else "No"
        majorities = info["majorities"]
        cols = " | ".join(str(majorities.get(rn, "")) for rn in run_names)
        md.append(f"| {company} | {stable} | {cols} |")

    md.extend([
        "",
        f"- Majority agreement rate: **{result['majorityAgreementRate']:.2%}**",
        f"- Recommendation drift count: **{result['recommendationDriftCount']}**",
    ])

    md.extend(["", "## Disagreement Rate Across Runs", ""])
    for rn, dr in result["perRunDisagreementRate"].items():
        md.append(f"- {rn}: {dr}")
    md.append(f"- Stddev: {result['disagreementRateStddev']}")

    md.extend(["", "## Class Distribution Per Run", ""])
    md.append("| Run | " + " | ".join(sorted(result["perRunClassDistribution"].get(
        run_names[0], {}
    ).keys())) + " |")
    md.append("|-----|" + "|".join(["---"] * len(result["perRunClassDistribution"].get(
        run_names[0], {}
    ))) + "|")
    for rn, dist in result["perRunClassDistribution"].items():
        cols = " | ".join(str(dist[c]) for c in sorted(dist.keys()))
        md.append(f"| {rn} | {cols} |")

    md.extend(["", "## Class Distribution Drift (Pairwise Absolute Delta)", ""])
    for pair, deltas in result["classDistributionDriftPairwise"].items():
        md.append(f"- **{pair}**: " + ", ".join(f"{c}={v}" for c, v in deltas.items()))

    md.extend(["", "## Conviction Drift by Agent", ""])
    for agent, runs in result["convictionDriftByAgent"].items():
        md.append(f"### {agent}")
        for rn, stats in runs.items():
            md.append(f"- {rn}: mean={stats['mean']}, median={stats['median']}")
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
        json_path, md_path = _write_stability_report(result, Path(output_dir))

        logger.info(
            "stability_analysis_completed",
            overall_pass=result["overallPass"],
            majority_agreement=result["majorityAgreementRate"],
            drift_count=result["recommendationDriftCount"],
            disagreement_stddev=result["disagreementRateStddev"],
            report_json=str(json_path),
            report_md=str(md_path),
        )
    finally:
        conn.close()


if __name__ == "__main__":
    app()
