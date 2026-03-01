#!/usr/bin/env python3
"""CLI script to run the full walk-forward backtest pipeline.

Trains a HistGradientBoostingClassifier per walk-forward window,
scores deals, simulates portfolios, and evaluates against baselines.
"""

from __future__ import annotations

import math
from collections import Counter
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
from startuplens.model.train import score_deals, train_model

logger = structlog.get_logger(__name__)
app = typer.Typer()

# Feature columns to load from the matview for model training
_FEATURE_QUERY = """
    SELECT DISTINCT ON (tfw.entity_id, tfw.as_of_date)
        tfw.entity_id::text,
        tfw.as_of_date::text AS campaign_date,
        tfw.sector,
        tfw.platform,
        tfw.country,
        tfw.company_age_months,
        tfw.employee_count,
        tfw.revenue_at_raise,
        tfw.pre_revenue,
        tfw.total_assets,
        tfw.total_debt,
        tfw.debt_to_asset_ratio,
        tfw.cash_position,
        tfw.funding_target,
        tfw.amount_raised,
        tfw.overfunding_ratio,
        tfw.instrument_type,
        COALESCE(tfw.revenue_at_raise > 0, false) AS has_revenue,
        COALESCE(tfw.qualified_institutional, false) AS has_institutional_coinvestor,
        COALESCE(tfw.eis_seis_eligible, false) AS eis_eligible,
        COALESCE(co.outcome, 'unknown') AS outcome
    FROM training_features_wide tfw
    LEFT JOIN companies c ON c.entity_id = tfw.entity_id
    LEFT JOIN crowdfunding_outcomes co
        ON co.company_id = c.id AND co.label_quality_tier <= 2
    WHERE tfw.as_of_date BETWEEN %s AND %s
    ORDER BY tfw.entity_id, tfw.as_of_date, co.campaign_date DESC NULLS LAST
"""


def _load_deals(conn, start: str, end: str) -> list[dict]:
    """Load feature rows for a date range."""
    return execute_query(conn, _FEATURE_QUERY, (start, end))


def _rows_to_scored_deals(rows: list[dict], scores: list[float] | None = None) -> list[ScoredDeal]:
    """Convert raw DB rows to ScoredDeal objects."""
    return [
        ScoredDeal(
            entity_id=r["entity_id"],
            score=scores[i] if scores else 0.0,
            sector=r.get("sector") or "unknown",
            platform=r.get("platform") or "unknown",
            campaign_date=r["campaign_date"],
            has_revenue=bool(r.get("has_revenue")),
            has_institutional_coinvestor=bool(r.get("has_institutional_coinvestor")),
            eis_eligible=bool(r.get("eis_eligible")),
            outcome=r.get("outcome", "unknown"),
        )
        for i, r in enumerate(rows)
    ]


@app.command()
def main(
    model_family: str = typer.Option("UK_Seed", help="Model family to evaluate"),
    max_per_year: int = typer.Option(5, help="Max investments per year"),
    check_size: float = typer.Option(10_000.0, help="Check size per investment"),
    max_per_sector: int = typer.Option(2, help="Max investments per sector per year"),
) -> None:
    """Run walk-forward backtest with ML model and baselines."""
    settings = get_settings()
    conn = get_connection(settings)
    policy = InvestorPolicy(
        max_investments_per_year=max_per_year,
        check_size=check_size,
        max_per_sector_per_year=max_per_sector,
    )

    try:
        windows = generate_walk_forward_windows()
        prior_deals: list[ScoredDeal] = []
        window_results: list[dict] = []
        all_model_aucs: list[float] = []
        all_model_eces: list[float] = []

        for window in windows:
            # Load train and test data as full feature rows
            train_rows = _load_deals(
                conn, window.train_start.isoformat(), window.train_end.isoformat(),
            )
            test_rows = _load_deals(
                conn, window.test_start.isoformat(), window.test_end.isoformat(),
            )

            logger.info(
                "loaded_window",
                window=window.label,
                train=len(train_rows),
                test=len(test_rows),
            )

            if not test_rows:
                continue

            # Train model on labeled data from train window
            train_labeled = [r for r in train_rows if r.get("outcome") in ("failed", "trading")]
            test_labeled = [r for r in test_rows if r.get("outcome") in ("failed", "trading")]

            model_scored_deals = None
            if len(train_labeled) >= 50 and len(test_labeled) >= 10:
                trained = train_model(train_rows, test_rows)
                all_model_aucs.append(trained.auc)
                all_model_eces.append(trained.ece)

                # Score ALL test deals (including unknown) for portfolio simulation
                model_scores = score_deals(trained, test_rows)
                model_scored_deals = _rows_to_scored_deals(test_rows, model_scores)

                logger.info(
                    "model_trained",
                    window=window.label,
                    auc=f"{trained.auc:.3f}",
                    ece=f"{trained.ece:.3f}",
                    n_train=trained.n_train,
                    n_test=trained.n_test,
                    top_features=sorted(
                        trained.feature_importances.items(),
                        key=lambda x: x[1], reverse=True,
                    )[:5],
                )
            else:
                logger.warning(
                    "insufficient_labeled_data",
                    window=window.label,
                    train_labeled=len(train_labeled),
                    test_labeled=len(test_labeled),
                )

            # Baselines (use ScoredDeal objects)
            deals = _rows_to_scored_deals(test_rows)
            random_scored = random_baseline(deals)
            heuristic_scored = heuristic_baseline(deals)
            momentum_scored = sector_momentum_baseline(deals, prior_deals or deals)

            # Simulate portfolios
            random_pf = simulate_portfolio(random_scored, policy)
            heuristic_pf = simulate_portfolio(heuristic_scored, policy)
            momentum_pf = simulate_portfolio(momentum_scored, policy)
            model_pf = (
                simulate_portfolio(model_scored_deals, policy)
                if model_scored_deals else None
            )

            random_fail = random_pf.failure_rate if random_pf.failure_rate > 0 else 1.0
            model_fail_vs_random = (
                model_pf.failure_rate / random_fail
                if model_pf else math.nan
            )

            result = {
                "window": window.label,
                "deals": len(test_rows),
                "labeled": len(test_labeled),
                "random_failure_rate": random_pf.failure_rate,
                "heuristic_failure_rate": heuristic_pf.failure_rate,
                "momentum_failure_rate": momentum_pf.failure_rate,
                "model_failure_rate": model_pf.failure_rate if model_pf else None,
                "model_fail_vs_random": model_fail_vs_random,
                "model_abstention_rate": model_pf.abstention_rate if model_pf else None,
            }

            # Compute max sector share from model portfolio
            if model_pf and model_pf.selected_deals:
                sector_counts = Counter(d.sector for d in model_pf.selected_deals)
                max_share = max(sector_counts.values()) / len(model_pf.selected_deals)
                result["max_sector_share"] = max_share

            window_results.append(result)

            logger.info(
                "window_result",
                window=window.label,
                random_fail=f"{random_pf.failure_rate:.3f}",
                heuristic_fail=f"{heuristic_pf.failure_rate:.3f}",
                model_fail=f"{model_pf.failure_rate:.3f}" if model_pf else "N/A",
            )

            prior_deals.extend(deals)

        if not window_results:
            logger.warning("no_deals_found")
            return

        # Aggregate metrics across windows
        n = len(window_results)
        model_windows = [w for w in window_results if w.get("model_failure_rate") is not None]
        n_model = len(model_windows)

        avg_model_fail_vs_random = (
            sum(w["model_fail_vs_random"] for w in model_windows) / n_model
            if n_model > 0 else math.nan
        )
        avg_model_abstention = (
            sum(w["model_abstention_rate"] for w in model_windows) / n_model
            if n_model > 0 else math.nan
        )
        avg_model_auc = (
            sum(all_model_aucs) / len(all_model_aucs)
            if all_model_aucs else math.nan
        )
        avg_model_ece = (
            sum(all_model_eces) / len(all_model_eces)
            if all_model_eces else math.nan
        )
        avg_max_sector = (
            sum(w.get("max_sector_share", 0) for w in model_windows) / n_model
            if n_model > 0 else math.nan
        )

        metrics = evaluate_backtest(
            survival_auc=avg_model_auc,
            calibration_ece=avg_model_ece,
            portfolio_moic_vs_random=math.nan,  # No returns data yet
            portfolio_failure_rate_vs_random=avg_model_fail_vs_random,
            claude_text_score_auc=math.nan,  # Phase 4+: requires Claude scoring
            progress_auc=math.nan,  # Phase 4+: requires progress model
            abstention_rate=avg_model_abstention,
            max_sector_share=avg_max_sector,
        )

        all_passed = all_must_pass_met(metrics)

        # Log provenance
        def _safe(v: float) -> float | None:
            return None if math.isnan(v) else v

        metrics_dict = {m.name: _safe(m.value) for m in metrics}
        pass_fail_dict = {
            m.name: {"value": _safe(m.value), "threshold": m.threshold, "passed": m.passed}
            for m in metrics
        }
        baselines_dict = {"per_window": window_results}

        features_active = list(
            trained.feature_importances.keys()
        ) if "trained" in dir() else []

        run_id = log_backtest_run(
            conn,
            model_family=model_family,
            data_snapshot_date=date.today(),
            train_window="2016-2022",
            test_window="2019-2025",
            features_active=features_active,
            metrics=metrics_dict,
            baselines=baselines_dict,
            pass_fail=pass_fail_dict,
            all_passed=all_passed,
            notes=f"Walk-forward backtest with HistGBT model ({n_model} windows trained)",
        )

        conn.commit()
        logger.info(
            "backtest_complete",
            run_id=run_id,
            all_passed=all_passed,
            windows_evaluated=n,
            windows_with_model=n_model,
            metrics={m.name: f"{m.value:.3f}" for m in metrics},
        )

    finally:
        conn.close()


if __name__ == "__main__":
    app()
