#!/usr/bin/env python3
"""Curate a high-evidence demo set and pre-generate analyst memo drafts."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
import typer
from run_synthetic_analyst_pilot_agents import _load_cycle, _load_cycle_items  # noqa: E402

from startuplens.config import get_settings
from startuplens.db import get_connection

logger = structlog.get_logger(__name__)
app = typer.Typer()


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "company"


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {}
    return {}


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _evidence_rank(item: dict[str, Any]) -> tuple[float, float, int]:
    suff = item.get("data_sufficiency", {})
    score = float(suff.get("score") or 0.0)
    cats = int(suff.get("category_count") or 0)
    has_model = 1 if item.get("model_recommendation") else 0
    return (score, float(cats), has_model)


def _memo_markdown(item: dict[str, Any]) -> str:
    suff = item.get("data_sufficiency", {})
    score = item.get("score")
    recommendation = item.get("model_recommendation") or "n/a"
    category_scores = _as_dict(item.get("category_scores"))
    risk_flags = _as_list(item.get("risk_flags"))
    missing = _as_list(item.get("missing_data_fields"))
    valuation = _as_dict(item.get("valuation_analysis"))

    lines = [
        f"# Demo Memo: {item.get('company_name')}",
        "",
        f"- Recommendation: **{recommendation}**",
        f"- Quant score: **{score if score is not None else 'n/a'}**",
        f"- Data sufficiency: **{suff.get('score', 'n/a')}**",
        f"- Evidence categories populated: **{suff.get('category_count', 'n/a')}**",
        f"- Sector/Country: **{item.get('sector') or 'n/a'} / {item.get('country') or 'n/a'}**",
        "",
        "## Terms Snapshot",
        f"- Instrument: {item.get('instrument_type') or 'n/a'}",
        f"- Amount raised: {item.get('amount_raised') or 'n/a'}",
        f"- Pre-money valuation: {item.get('pre_money_valuation') or 'n/a'}",
        f"- Investors: {item.get('cf_investor_count') or 'n/a'}",
        "",
        "## Category Scores",
    ]
    if category_scores:
        for key, value in category_scores.items():
            lines.append(f"- {key}: {value}")
    else:
        lines.append("- n/a")

    lines.extend(["", "## Risks"])
    if risk_flags:
        for risk in risk_flags[:8]:
            lines.append(f"- {risk}")
    else:
        lines.append("- n/a")

    lines.extend(["", "## Missing Data"])
    if missing:
        for field in missing[:8]:
            lines.append(f"- {field}")
    else:
        lines.append("- n/a")

    lines.extend(["", "## Valuation Context"])
    if valuation:
        for key in ("entry_multiple", "sector_median", "valuation_percentile", "signal"):
            if key in valuation:
                lines.append(f"- {key}: {valuation.get(key)}")
    else:
        lines.append("- n/a")

    lines.extend(
        [
            "",
            f"_Generated at {datetime.now(UTC).isoformat()}_",
        ]
    )
    return "\n".join(lines)


@app.command()
def main(
    cycle_id: str | None = typer.Option(
        None,
        help="Shadow cycle UUID. Defaults to latest active cycle.",
    ),
    top_n: int = typer.Option(
        5,
        help="Number of curated demo companies (3-5 recommended).",
    ),
    max_items: int = typer.Option(
        2000,
        help="Maximum shadow-cycle items to scan.",
    ),
    min_sufficiency_score: float = typer.Option(
        60.0,
        help="Minimum data sufficiency score for demo candidates.",
    ),
    min_category_count: int = typer.Option(
        3,
        help="Minimum evidence categories for demo candidates.",
    ),
    output_dir: str = typer.Option(
        "reports/demo",
        help="Output directory for curated demo artifacts.",
    ),
) -> None:
    if top_n < 1:
        raise typer.BadParameter("top_n must be >= 1")
    if max_items < 1:
        raise typer.BadParameter("max_items must be >= 1")

    settings = get_settings()
    conn = get_connection(settings)
    try:
        cycle = _load_cycle(conn, cycle_id)
        rows = _load_cycle_items(conn, cycle["id"], max_items)
        if not rows:
            raise typer.BadParameter("No shadow-cycle items found.")

        eligible = [
            row
            for row in rows
            if (row.get("data_sufficiency", {}).get("score") or 0) >= min_sufficiency_score
            and (row.get("data_sufficiency", {}).get("category_count") or 0) >= min_category_count
            and (row.get("model_recommendation") or "").lower() in {
                "invest",
                "deep_diligence",
                "watch",
                "pass",
            }
        ]
        if not eligible:
            raise typer.BadParameter("No demo-eligible items after evidence filters.")

        eligible.sort(key=_evidence_rank, reverse=True)
        selected = eligible[:top_n]

        base = Path(output_dir) / f"{cycle['cycle_name']}_{datetime.now(UTC).date().isoformat()}"
        base.mkdir(parents=True, exist_ok=True)

        items_file = base / "curated_item_ids.txt"
        items_file.write_text(
            "\n".join(str(item["shadow_cycle_item_id"]) for item in selected) + "\n",
            encoding="utf-8",
        )

        memo_paths: list[str] = []
        manifest_items: list[dict[str, Any]] = []
        for idx, item in enumerate(selected, start=1):
            memo_path = base / f"{idx:02d}_{_slug(str(item.get('company_name') or 'company'))}.md"
            memo_path.write_text(_memo_markdown(item), encoding="utf-8")
            memo_paths.append(str(memo_path))
            manifest_items.append({
                "shadow_cycle_item_id": item.get("shadow_cycle_item_id"),
                "company_name": item.get("company_name"),
                "recommendation": item.get("model_recommendation"),
                "score": item.get("score"),
                "data_sufficiency": item.get("data_sufficiency", {}).get("score"),
                "category_count": item.get("data_sufficiency", {}).get("category_count"),
                "memo_path": str(memo_path),
            })

        manifest = {
            "cycle_id": cycle["id"],
            "cycle_name": cycle["cycle_name"],
            "generated_at": datetime.now(UTC).isoformat(),
            "selected_count": len(selected),
            "item_ids_file": str(items_file),
            "items": manifest_items,
        }
        manifest_path = base / "demo_manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        logger.info(
            "demo_curation_complete",
            cycle_id=cycle["id"],
            cycle_name=cycle["cycle_name"],
            selected=len(selected),
            manifest_path=str(manifest_path),
        )
        typer.echo(json.dumps(manifest, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    app()
