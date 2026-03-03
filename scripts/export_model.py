#!/usr/bin/env python3
"""Export trained HistGradientBoostingClassifier to JSON for browser inference.

Extracts the tree structure, feature metadata, and calibration mapping
into a portable JSON format that can be loaded by the TypeScript
tree-walker in the web app.

Usage:
    python scripts/export_model.py [--output web/public/model/model.json]
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import structlog
import typer

from startuplens.backtest.splitter import generate_walk_forward_windows
from startuplens.config import get_settings
from startuplens.db import execute_query, get_connection, refresh_matview
from startuplens.model.train import (
    CATEGORICAL_FEATURES,
    FEATURE_COLUMNS,
    TrainedModel,
    train_model,
)

logger = structlog.get_logger(__name__)
app = typer.Typer()


def _extract_trees(clf) -> list[dict]:
    """Extract tree structure from HistGradientBoostingClassifier."""
    trees = []
    for iteration in clf._predictors:
        for tree_predictor in iteration:
            nodes = tree_predictor.nodes
            tree_nodes = []
            for node in nodes:
                threshold = float(node["num_threshold"])
                if np.isinf(threshold) or np.isnan(threshold):
                    threshold = 0.0  # Leaf nodes don't use threshold
                tree_nodes.append({
                    "left": int(node["left"]),
                    "right": int(node["right"]),
                    "feature_idx": int(node["feature_idx"]),
                    "threshold": threshold,
                    "value": float(node["value"]),
                    "is_leaf": bool(node["is_leaf"]),
                    "missing_go_to_left": bool(node["missing_go_to_left"]),
                })
            trees.append(tree_nodes)
    return trees


def _extract_calibration(model) -> dict | None:
    """Extract isotonic calibration mapping if present."""
    from sklearn.calibration import CalibratedClassifierCV

    if not isinstance(model, CalibratedClassifierCV):
        return None

    # CalibratedClassifierCV stores calibrators in calibrated_classifiers_
    # Each has calibrators[0] which is an _CalibratedClassifier
    # We need the isotonic regression mapping
    calibrators = []
    for cal_clf in model.calibrated_classifiers_:
        for calibrator in cal_clf.calibrators:
            if hasattr(calibrator, "X_thresholds_") and hasattr(calibrator, "y_thresholds_"):
                calibrators.append({
                    "x": calibrator.X_thresholds_.tolist(),
                    "y": calibrator.y_thresholds_.tolist(),
                })

    if not calibrators:
        return None

    # Average the calibration mappings from CV folds
    # Use the first one's x-points and interpolate all y-values
    ref_x = calibrators[0]["x"]
    avg_y = np.zeros(len(ref_x))
    for cal in calibrators:
        avg_y += np.interp(ref_x, cal["x"], cal["y"])
    avg_y /= len(calibrators)

    return {
        "x": [float(v) for v in ref_x],
        "y": [float(v) for v in avg_y],
    }


def export_model(trained: TrainedModel, output_path: Path) -> None:
    """Export a trained model to JSON format."""
    from sklearn.calibration import CalibratedClassifierCV

    # Unwrap calibrated model to get the base classifier
    if isinstance(trained.model, CalibratedClassifierCV):
        base_clf = trained.model.calibrated_classifiers_[0].estimator
        calibration = _extract_calibration(trained.model)
    else:
        base_clf = trained.model
        calibration = None

    trees = _extract_trees(base_clf)
    baseline = float(np.asarray(base_clf._baseline_prediction).flat[0])
    learning_rate = float(base_clf.learning_rate)

    model_json = {
        "format": "hgbt_tree_walker_v1",
        "feature_names": FEATURE_COLUMNS + CATEGORICAL_FEATURES,
        "numeric_features": FEATURE_COLUMNS,
        "categorical_features": CATEGORICAL_FEATURES,
        "baseline_prediction": baseline,
        "learning_rate": learning_rate,
        "trees": trees,
        "calibration": calibration,
        "metrics": {
            "auc": trained.auc,
            "ece": trained.ece,
            "n_train": trained.n_train,
            "n_test": trained.n_test,
        },
        "feature_importances": trained.feature_importances,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(model_json, f, indent=2)

    size_kb = output_path.stat().st_size / 1024
    logger.info(
        "model_exported",
        path=str(output_path),
        trees=len(trees),
        size_kb=f"{size_kb:.1f}",
        has_calibration=calibration is not None,
    )


@app.command()
def main(
    output: str = typer.Option(
        "web/public/model/model.json",
        help="Output path for the JSON model",
    ),
) -> None:
    """Train on all available data and export to JSON."""
    settings = get_settings()
    conn = get_connection(settings)

    try:
        # Refresh the materialized view so it reflects current feature_store data.
        logger.info("refreshing_matview")
        refresh_matview(conn)
        logger.info("matview_refreshed")

        # Use all windows to get the most data for the final model
        windows = generate_walk_forward_windows()
        last_window = windows[-1]

        # Load train data from the full range
        query = """
            SELECT DISTINCT ON (tfw.entity_id, tfw.as_of_date)
                tfw.entity_id::text,
                c.id::text AS company_id,
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
                COALESCE(co.outcome, 'unknown') AS outcome
            FROM training_features_wide tfw
            LEFT JOIN companies c ON c.entity_id = tfw.entity_id
            LEFT JOIN crowdfunding_outcomes co
                ON co.company_id = c.id AND co.label_quality_tier <= 2
            WHERE tfw.as_of_date BETWEEN %s AND %s
            ORDER BY tfw.entity_id, tfw.as_of_date, co.campaign_date DESC NULLS LAST
        """

        all_rows = execute_query(
            conn,
            query,
            (last_window.train_start.isoformat(), last_window.test_end.isoformat()),
        )

        # Split 80/20 for training and evaluation
        labeled = [r for r in all_rows if r.get("outcome") in ("failed", "trading")]
        split = int(len(labeled) * 0.8)
        train_rows = labeled[:split]
        test_rows = labeled[split:]

        logger.info(
            "training_for_export",
            total=len(all_rows),
            labeled=len(labeled),
            train=len(train_rows),
            test=len(test_rows),
        )

        trained = train_model(train_rows, test_rows)
        logger.info(
            "model_trained",
            auc=f"{trained.auc:.3f}",
            ece=f"{trained.ece:.3f}",
        )

        export_model(trained, Path(output))
        typer.echo(f"Model exported to {output}")

    finally:
        conn.close()


if __name__ == "__main__":
    app()
