#!/usr/bin/env python3
"""Seed deterministic evaluations for shadow-cycle coverage.

This raises model-alignment coverage before synthetic analyst pilots by
ensuring a minimum fraction of pilot-ready items have an evaluation record.
"""

from __future__ import annotations

import json
from typing import Any

import structlog
import typer
from run_synthetic_analyst_pilot_agents import (  # noqa: E402
    _load_cycle,
    _load_cycle_items,
    _seed_missing_model_evaluations,
)

from startuplens.config import get_settings
from startuplens.db import get_connection

logger = structlog.get_logger(__name__)
app = typer.Typer()


@app.command()
def main(
    cycle_id: str | None = typer.Option(
        None,
        help="Optional shadow cycle UUID. Defaults to latest active cycle.",
    ),
    max_items: int = typer.Option(
        5000,
        help="Maximum cycle items to consider for seeding.",
    ),
    min_sufficiency_score: float = typer.Option(
        50.0,
        help="Minimum data sufficiency score for seed eligibility.",
    ),
    min_category_count: int = typer.Option(
        3,
        help="Minimum evidence category count for seed eligibility.",
    ),
    min_model_coverage: float = typer.Option(
        0.50,
        help="Target minimum model-evaluation coverage for eligible items.",
    ),
) -> None:
    if max_items < 1:
        raise typer.BadParameter("max_items must be >= 1")
    if not (0.0 <= min_sufficiency_score <= 100.0):
        raise typer.BadParameter("min_sufficiency_score must be in [0, 100]")
    if min_category_count < 1:
        raise typer.BadParameter("min_category_count must be >= 1")
    if not (0.0 <= min_model_coverage <= 1.0):
        raise typer.BadParameter("min_model_coverage must be in [0, 1]")

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
        ]
        if not eligible:
            raise typer.BadParameter(
                "No eligible items after sufficiency/category filters."
            )

        before_with_model = sum(1 for row in eligible if row.get("model_recommendation"))
        before_coverage = before_with_model / len(eligible)

        seeded = _seed_missing_model_evaluations(
            conn,
            eligible,
            min_model_coverage=min_model_coverage,
        )

        after_with_model = sum(1 for row in eligible if row.get("model_recommendation"))
        after_coverage = after_with_model / len(eligible)

        payload: dict[str, Any] = {
            "cycle_id": cycle["id"],
            "cycle_name": cycle["cycle_name"],
            "eligible_items": len(eligible),
            "seeded_evaluations": seeded,
            "coverage_before": round(before_coverage, 4),
            "coverage_after": round(after_coverage, 4),
            "target_coverage": round(min_model_coverage, 4),
        }

        logger.info("seed_shadow_evaluations_complete", **payload)
        typer.echo(json.dumps(payload, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    app()
