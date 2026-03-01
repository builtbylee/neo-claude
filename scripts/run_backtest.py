#!/usr/bin/env python3
"""CLI script to run the full walk-forward backtest pipeline."""

from __future__ import annotations

from datetime import date

import structlog
import typer

from startuplens.backtest.baselines import (
    ScoredDeal,
    heuristic_baseline,
    random_baseline,
    sector_momentum_baseline,
)
from startuplens.backtest.metrics import all_must_pass_met, evaluate_backtest
from startuplens.backtest.provenance import log_backtest_run
from startuplens.backtest.simulator import InvestorPolicy, simulate_portfolio
from startuplens.backtest.splitter import generate_walk_forward_windows
from startuplens.config import get_settings
from startuplens.db import execute_query, get_connection

logger = structlog.get_logger(__name__)
app = typer.Typer()


def _load_deals_for_window(conn, window) -> list[ScoredDeal]:
    """Load deal data for a given time window from the database.

    This is the bridge function connecting the feature store to the backtest
    pipeline. It queries the training_features_wide materialized view.
    """
    rows = execute_query(
        conn,
        """
        SELECT DISTINCT ON (tfw.entity_id, tfw.as_of_date)
            tfw.entity_id::text,
            tfw.sector,
            tfw.platform,
            tfw.as_of_date::text AS campaign_date,
            COALESCE(tfw.revenue_at_raise > 0, false) AS has_revenue,
            COALESCE(tfw.qualified_institutional, false) AS has_institutional_coinvestor,
            COALESCE(tfw.eis_seis_eligible, false) AS eis_eligible,
            COALESCE(co.outcome, 'unknown') AS outcome
        FROM training_features_wide tfw
        LEFT JOIN entity_links el
            ON el.entity_id = tfw.entity_id
        LEFT JOIN companies c
            ON c.id::text = el.source_identifier AND c.source = el.source
        LEFT JOIN crowdfunding_outcomes co
            ON co.company_id = c.id AND co.label_quality_tier <= 2
        WHERE tfw.as_of_date BETWEEN %s AND %s
        ORDER BY tfw.entity_id, tfw.as_of_date, el.confidence DESC
        """,
        (window.test_start.isoformat(), window.test_end.isoformat()),
    )

    return [
        ScoredDeal(
            entity_id=r["entity_id"],
            score=0.0,
            sector=r.get("sector") or "unknown",
            platform=r.get("platform") or "unknown",
            campaign_date=r["campaign_date"],
            has_revenue=bool(r.get("has_revenue")),
            has_institutional_coinvestor=bool(r.get("has_institutional_coinvestor")),
            eis_eligible=bool(r.get("eis_eligible")),
            outcome=r.get("outcome", "unknown"),
        )
        for r in rows
    ]


@app.command()
def main(
    model_family: str = typer.Option("UK_Seed", help="Model family to evaluate"),
    max_per_year: int = typer.Option(2, help="Max investments per year"),
    check_size: float = typer.Option(10_000.0, help="Check size per investment"),
    max_per_sector: int = typer.Option(1, help="Max investments per sector per year"),
) -> None:
    """Run walk-forward backtest with baselines and metrics."""
    settings = get_settings()
    conn = get_connection(settings)
    policy = InvestorPolicy(
        max_investments_per_year=max_per_year,
        check_size=check_size,
        max_per_sector_per_year=max_per_sector,
    )

    try:
        windows = generate_walk_forward_windows()
        all_deals: list[ScoredDeal] = []

        for window in windows:
            deals = _load_deals_for_window(conn, window)
            logger.info(
                "loaded_window",
                window=window.label,
                deals=len(deals),
            )
            all_deals.extend(deals)

        if not all_deals:
            logger.warning("no_deals_found")
            return

        # Run baselines
        random_scored = random_baseline(all_deals)
        heuristic_scored = heuristic_baseline(all_deals)
        momentum_scored = sector_momentum_baseline(all_deals, all_deals)

        # Simulate portfolios
        random_portfolio = simulate_portfolio(random_scored, policy)
        heuristic_portfolio = simulate_portfolio(heuristic_scored, policy)
        momentum_portfolio = simulate_portfolio(momentum_scored, policy)

        # Evaluate metrics (using random baseline as reference)
        # Compute ratios that evaluate_backtest expects
        random_moic = random_portfolio.moic or 1.0
        moic_vs_random = 1.0 / random_moic if random_moic else 1.0  # placeholder model MOIC
        random_fail = random_portfolio.failure_rate or 1.0
        fail_vs_random = heuristic_portfolio.failure_rate / random_fail if random_fail else 1.0

        metrics = evaluate_backtest(
            survival_auc=0.5,  # Placeholder — real model AUC goes here
            calibration_ece=0.5,
            portfolio_moic_vs_random=moic_vs_random,
            portfolio_failure_rate_vs_random=fail_vs_random,
            claude_text_score_auc=0.5,
            progress_auc=0.5,
            abstention_rate=heuristic_portfolio.abstention_rate,
            max_sector_share=0.0,
        )

        all_passed = all_must_pass_met(metrics)

        # Log provenance
        metrics_dict = {m.name: m.value for m in metrics}
        pass_fail_dict = {
            m.name: {"value": m.value, "threshold": m.threshold, "passed": m.passed}
            for m in metrics
        }
        baselines_dict = {
            "random": {"failure_rate": random_portfolio.failure_rate},
            "heuristic": {"failure_rate": heuristic_portfolio.failure_rate},
            "momentum": {"failure_rate": momentum_portfolio.failure_rate},
        }

        run_id = log_backtest_run(
            conn,
            model_family=model_family,
            data_snapshot_date=date.today(),
            train_window="2016-2022",
            test_window="2023-2025",
            features_active=[],
            metrics=metrics_dict,
            baselines=baselines_dict,
            pass_fail=pass_fail_dict,
            all_passed=all_passed,
            notes="CLI backtest run",
        )

        conn.commit()
        logger.info(
            "backtest_complete",
            run_id=run_id,
            all_passed=all_passed,
            metrics={m.name: f"{m.value:.3f}" for m in metrics},
        )

    finally:
        conn.close()


if __name__ == "__main__":
    app()
