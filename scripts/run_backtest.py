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
from startuplens.backtest.simulator import (
    InvestorPolicy,
    compute_portfolio_quality,
    simulate_portfolio,
)
from startuplens.backtest.splitter import generate_walk_forward_windows
from startuplens.backtest.text_score_auc import compute_claude_text_auc
from startuplens.config import get_settings
from startuplens.db import execute_query, get_connection, refresh_matview
from startuplens.model.progress_labels import load_progress_labels
from startuplens.model.train import (
    filter_rows_for_family,
    score_deals,
    train_model,
    train_progress_model,
)

logger = structlog.get_logger(__name__)
app = typer.Typer()

# Feature columns to load from the matview for model training
_FEATURE_QUERY = """
    SELECT DISTINCT ON (tfw.entity_id, tfw.as_of_date)
        tfw.entity_id::text,
        c.id::text AS company_id,
        tfw.as_of_date::text AS campaign_date,
        tfw.sector,
        tfw.platform,
        tfw.country,
        COALESCE(co.stage_bucket, 'unknown') AS stage_bucket,
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
        COALESCE(co.outcome, 'unknown') AS outcome,
        fd.revenue_growth_yoy
    FROM training_features_wide tfw
    LEFT JOIN companies c ON c.entity_id = tfw.entity_id
    LEFT JOIN crowdfunding_outcomes co
        ON co.company_id = c.id
        AND co.label_quality_tier <= 2
        AND co.campaign_date = tfw.as_of_date
    LEFT JOIN LATERAL (
        SELECT revenue_growth_yoy
        FROM financial_data
        WHERE company_id = c.id
          AND revenue_growth_yoy IS NOT NULL
          AND period_end_date <= tfw.as_of_date
        ORDER BY period_end_date DESC
        LIMIT 1
    ) fd ON true
    WHERE tfw.as_of_date BETWEEN %s AND %s
    ORDER BY tfw.entity_id, tfw.as_of_date, co.campaign_date DESC NULLS LAST, c.id
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
            revenue_growth=r.get("revenue_growth_yoy"),
        )
        for i, r in enumerate(rows)
    ]


_GROWTH_FEATURE_QUERY = """
    SELECT DISTINCT ON (r.company_id, r.as_of_date)
        r.company_id::text,
        r.as_of_date::text AS campaign_date,
        CASE WHEN fd_p.revenue IS NOT NULL AND fd_p.revenue > 0
             THEN (fd_r.revenue - fd_p.revenue) / fd_p.revenue
             ELSE NULL END AS revenue_growth_yoy,
        CASE WHEN fd_p.total_assets IS NOT NULL AND fd_p.total_assets > 0
             THEN (fd_r.total_assets - fd_p.total_assets) / fd_p.total_assets
             ELSE NULL END AS asset_growth_yoy,
        CASE WHEN fd_p.cash_and_equivalents IS NOT NULL
                  AND fd_p.cash_and_equivalents > 0
             THEN (fd_r.cash_and_equivalents - fd_p.cash_and_equivalents)
                  / fd_p.cash_and_equivalents
             ELSE NULL END AS cash_growth_yoy,
        CASE WHEN fd_p.net_income IS NOT NULL
             THEN fd_r.net_income - fd_p.net_income
             ELSE NULL END AS net_income_improvement
    FROM unnest(%s::uuid[], %s::date[]) AS r(company_id, as_of_date)
    JOIN financial_data fd_r
        ON fd_r.company_id = r.company_id
        AND fd_r.period_type = 'annual'
        AND fd_r.period_end_date <= r.as_of_date
    JOIN financial_data fd_p
        ON fd_p.company_id = fd_r.company_id
        AND fd_p.period_type = 'prior_annual'
        AND fd_p.period_end_date = fd_r.period_end_date
    ORDER BY r.company_id, r.as_of_date, fd_r.period_end_date DESC
"""


def _enrich_growth_features(conn, rows: list[dict]) -> None:
    """Add YoY growth features per row, using each row's campaign_date as cutoff."""
    pairs = [
        (r["company_id"], r["campaign_date"])
        for r in rows
        if r.get("company_id") and r.get("campaign_date")
    ]
    if not pairs:
        return

    company_ids = [p[0] for p in pairs]
    as_of_dates = [p[1] for p in pairs]

    growth_rows = execute_query(
        conn, _GROWTH_FEATURE_QUERY, (company_ids, as_of_dates),
    )
    growth_by_key = {
        (r["company_id"], r["campaign_date"]): r for r in growth_rows
    }

    for row in rows:
        cid = row.get("company_id")
        cd = row.get("campaign_date")
        if cid and cd and (cid, cd) in growth_by_key:
            g = growth_by_key[(cid, cd)]
            row["revenue_growth_yoy"] = g.get("revenue_growth_yoy")
            row["asset_growth_yoy"] = g.get("asset_growth_yoy")
            row["cash_growth_yoy"] = g.get("cash_growth_yoy")
            row["net_income_improvement"] = g.get("net_income_improvement")


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
        # Refresh the materialized view so it reflects current feature_store data.
        # Without this, the matview contains a stale snapshot from migration time.
        logger.info("refreshing_matview")
        refresh_matview(conn)
        logger.info("matview_refreshed")

        windows = generate_walk_forward_windows()
        prior_deals: list[ScoredDeal] = []
        window_results: list[dict] = []
        all_model_aucs: list[float] = []
        all_model_eces: list[float] = []
        all_progress_aucs: list[float] = []

        for window in windows:
            # Load train and test data as full feature rows
            train_rows_all = _load_deals(
                conn, window.train_start.isoformat(), window.train_end.isoformat(),
            )
            test_rows_all = _load_deals(
                conn, window.test_start.isoformat(), window.test_end.isoformat(),
            )

            train_rows = filter_rows_for_family(train_rows_all, model_family)
            test_rows = filter_rows_for_family(test_rows_all, model_family)

            # Fallback to pooled rows if family sample is too small.
            if len(train_rows) < 120 or len(test_rows) < 30:
                logger.warning(
                    "family_sample_too_small_fallback_pooled",
                    family=model_family,
                    train_family=len(train_rows),
                    test_family=len(test_rows),
                )
                train_rows = train_rows_all
                test_rows = test_rows_all

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
            model_scores = None
            if len(train_labeled) >= 50 and len(test_labeled) >= 10:
                trained = train_model(train_rows, test_rows)
                all_model_aucs.append(trained.auc)
                all_model_eces.append(trained.ece)

                # Score ALL test deals (including unknown) for portfolio simulation
                model_scores = score_deals(trained, test_rows)
                # model_scored_deals built after enrichment below

                logger.info(
                    "model_trained",
                    window=window.label,
                    auc=f"{trained.auc:.3f}",
                    ece=f"{trained.ece:.3f}",
                    model_name=trained.model_name,
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

            # --- Enrich growth features (per-row temporal cutoff) ---
            _enrich_growth_features(conn, train_rows)
            _enrich_growth_features(conn, test_rows)

            # Build model ScoredDeals AFTER enrichment so revenue_growth
            # reflects per-row-capped values for quality metrics.
            if model_scores is not None:
                model_scored_deals = _rows_to_scored_deals(
                    test_rows, model_scores,
                )

            # --- Progress model ---
            train_progress = load_progress_labels(
                conn,
                window.train_start.isoformat(),
                window.train_end.isoformat(),
                cutoff_date=window.train_end.isoformat(),
            )
            test_progress = load_progress_labels(
                conn,
                window.test_start.isoformat(),
                window.test_end.isoformat(),
                cutoff_date=window.test_end.isoformat(),
            )

            progress_trained = train_progress_model(
                train_rows, test_rows, train_progress, test_progress,
            )
            if progress_trained is not None:
                all_progress_aucs.append(progress_trained.auc)
                logger.info(
                    "progress_model_trained",
                    window=window.label,
                    auc=f"{progress_trained.auc:.3f}",
                    n_train=progress_trained.n_train,
                    n_test=progress_trained.n_test,
                    top_features=sorted(
                        progress_trained.feature_importances.items(),
                        key=lambda x: x[1], reverse=True,
                    )[:5],
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

            # Portfolio quality scores (revenue-growth-weighted outcomes)
            random_quality = compute_portfolio_quality(random_pf)
            model_quality = (
                compute_portfolio_quality(model_pf) if model_pf else math.nan
            )
            quality_vs_random = (
                model_quality / random_quality
                if model_pf and random_quality > 0 else math.nan
            )

            # Model uncertainty: fraction of scores in uncertain band (40-60)
            uncertainty_rate = math.nan
            if model_scores:
                uncertain = sum(1 for s in model_scores if 40 <= s <= 60)
                uncertainty_rate = uncertain / len(model_scores)

            # Top-K sector concentration: sector share in top 50 model-ranked deals
            # Only meaningful when deals have real sector labels (not all "unknown")
            top_k_sector = math.nan
            if model_scored_deals:
                top_k = sorted(model_scored_deals, key=lambda d: d.score, reverse=True)[:50]
                known_sectors = [d.sector for d in top_k if d.sector != "unknown"]
                if known_sectors:
                    sector_counts = Counter(known_sectors)
                    top_k_sector = max(sector_counts.values()) / len(known_sectors)

            result = {
                "window": window.label,
                "deals": len(test_rows),
                "labeled": len(test_labeled),
                "random_failure_rate": random_pf.failure_rate,
                "heuristic_failure_rate": heuristic_pf.failure_rate,
                "momentum_failure_rate": momentum_pf.failure_rate,
                "model_failure_rate": model_pf.failure_rate if model_pf else None,
                "model_fail_vs_random": model_fail_vs_random,
                "model_uncertainty_rate": uncertainty_rate,
                "top_k_sector_concentration": top_k_sector,
                "model_quality": model_quality,
                "random_quality": random_quality,
                "quality_vs_random": quality_vs_random,
            }

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
        avg_model_auc = (
            sum(all_model_aucs) / len(all_model_aucs)
            if all_model_aucs else math.nan
        )
        avg_model_ece = (
            sum(all_model_eces) / len(all_model_eces)
            if all_model_eces else math.nan
        )
        uncertainty_windows = [
            w for w in model_windows if not math.isnan(w["model_uncertainty_rate"])
        ]
        avg_uncertainty = (
            sum(w["model_uncertainty_rate"] for w in uncertainty_windows)
            / len(uncertainty_windows)
            if uncertainty_windows else math.nan
        )
        sector_windows = [
            w for w in model_windows if not math.isnan(w["top_k_sector_concentration"])
        ]
        avg_top_k_sector = (
            sum(w["top_k_sector_concentration"] for w in sector_windows)
            / len(sector_windows)
            if sector_windows else math.nan
        )
        quality_windows = [
            w for w in model_windows
            if not math.isnan(w.get("quality_vs_random", math.nan))
        ]
        avg_quality_vs_random = (
            sum(w["quality_vs_random"] for w in quality_windows)
            / len(quality_windows)
            if quality_windows else math.nan
        )

        # Compute Claude text score AUC from stored scores
        claude_auc = compute_claude_text_auc(conn)

        metrics = evaluate_backtest(
            survival_auc=avg_model_auc,
            calibration_ece=avg_model_ece,
            portfolio_quality_vs_random=avg_quality_vs_random,
            portfolio_failure_rate_vs_random=avg_model_fail_vs_random,
            claude_text_score_auc=claude_auc if claude_auc > 0 else math.nan,
            progress_auc=(
                sum(all_progress_aucs) / len(all_progress_aucs)
                if all_progress_aucs else math.nan
            ),
            model_uncertainty_rate=avg_uncertainty,
            top_k_sector_concentration=avg_top_k_sector,
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
        # Sanitize NaN values in per-window results for JSON serialization
        def _sanitize_dict(d: dict) -> dict:
            return {
                k: (None if isinstance(v, float) and math.isnan(v) else v)
                for k, v in d.items()
            }

        baselines_dict = {"per_window": [_sanitize_dict(w) for w in window_results]}
        total_labeled_samples = sum(int(w.get("labeled", 0) or 0) for w in window_results)

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

        # Persist a compact governance snapshot for UI monitoring.
        try:
            execute_query(
                conn,
                """
                INSERT INTO model_health_snapshots (
                    model_version_id,
                    backtest_run_id,
                    survival_auc,
                    calibration_ece,
                    portfolio_quality_vs_random,
                    release_gate_open,
                    calibration_healthy,
                    retrain_recommended,
                    notes
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    None,
                    run_id,
                    None if math.isnan(avg_model_auc) else avg_model_auc,
                    None if math.isnan(avg_model_ece) else avg_model_ece,
                    None if math.isnan(avg_quality_vs_random) else avg_quality_vs_random,
                    all_passed,
                    (
                        not math.isnan(avg_model_ece)
                        and avg_model_ece <= 0.08
                    ),
                    (
                        math.isnan(avg_model_auc)
                        or avg_model_auc < 0.65
                        or (not math.isnan(avg_model_ece) and avg_model_ece > 0.1)
                    ),
                    f"Auto-snapshot for family {model_family}",
                ),
            )
        except Exception:  # noqa: BLE001
            logger.warning("model_health_snapshot_insert_failed")

        # Upsert segment-level evidence for quick-score gating.
        try:
            execute_query(
                conn,
                """
                INSERT INTO segment_model_evidence (
                    segment_key,
                    sample_size,
                    survival_auc,
                    calibration_ece,
                    release_gate_open,
                    last_backtest_run_id,
                    last_backtest_date,
                    source_coverage,
                    notes,
                    updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, now())
                ON CONFLICT (segment_key) DO UPDATE
                SET
                    sample_size = EXCLUDED.sample_size,
                    survival_auc = EXCLUDED.survival_auc,
                    calibration_ece = EXCLUDED.calibration_ece,
                    release_gate_open = EXCLUDED.release_gate_open,
                    last_backtest_run_id = EXCLUDED.last_backtest_run_id,
                    last_backtest_date = EXCLUDED.last_backtest_date,
                    source_coverage = EXCLUDED.source_coverage,
                    notes = EXCLUDED.notes,
                    updated_at = now()
                """,
                (
                    model_family,
                    total_labeled_samples,
                    None if math.isnan(avg_model_auc) else avg_model_auc,
                    None if math.isnan(avg_model_ece) else avg_model_ece,
                    all_passed,
                    run_id,
                    date.today(),
                    '{"pipeline":"walk_forward","data":"free_public_sources"}',
                    f"Auto-updated by run_backtest for {model_family}",
                ),
            )
        except Exception:  # noqa: BLE001
            logger.warning("segment_model_evidence_upsert_failed")

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
