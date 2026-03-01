"""Train and evaluate a gradient boosted classifier for deal scoring.

Uses scikit-learn's HistGradientBoostingClassifier which handles missing
values natively and performs on par with LightGBM for tabular data.
"""

from __future__ import annotations

import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.metrics import roc_auc_score

from startuplens.backtest.metrics import compute_ece

# Features used for training — must match training_features_wide columns.
# Ordered to match the matview query.
FEATURE_COLUMNS = [
    "company_age_months",
    "employee_count",
    "revenue_at_raise",
    "pre_revenue",
    "total_assets",
    "total_debt",
    "debt_to_asset_ratio",
    "cash_position",
    "funding_target",
    "amount_raised",
    "overfunding_ratio",
]

# Categorical features encoded as strings → need ordinal encoding
CATEGORICAL_FEATURES = ["instrument_type", "platform", "country"]

# Progress model uses base features + YoY growth features from prior/current FY.
PROGRESS_FEATURE_COLUMNS = FEATURE_COLUMNS + [
    "revenue_growth_yoy",
    "asset_growth_yoy",
    "cash_growth_yoy",
    "net_income_improvement",
]
PROGRESS_CATEGORICAL_FEATURES = CATEGORICAL_FEATURES


@dataclass
class TrainedModel:
    """Container for a trained model and its metadata."""

    model: Any
    feature_names: list[str]
    auc: float
    ece: float
    n_train: int
    n_test: int
    feature_importances: dict[str, float]


def _build_feature_matrix(
    rows: list[dict],
    feature_columns: list[str],
    categorical_columns: list[str],
) -> np.ndarray:
    """Convert list of dicts into a numpy feature matrix.

    Numeric features are passed through (None → NaN for sklearn).
    Categorical features are hashed to integers.
    """
    all_cols = feature_columns + categorical_columns
    n_rows = len(rows)
    n_cols = len(all_cols)
    X = np.full((n_rows, n_cols), np.nan)  # noqa: N806

    for i, row in enumerate(rows):
        for j, col in enumerate(all_cols):
            val = row.get(col)
            if val is None:
                continue
            if col in categorical_columns:
                # Hash string to integer for tree-based model
                X[i, j] = hash(str(val)) % 100_000
            elif isinstance(val, bool):
                X[i, j] = float(val)
            else:
                try:
                    X[i, j] = float(val)
                except (ValueError, TypeError):
                    continue

    return X


def _build_target(rows: list[dict]) -> np.ndarray:
    """Build binary target: failed=1, trading=0. Excludes unknown."""
    y = np.array([1 if r["outcome"] == "failed" else 0 for r in rows])
    return y


def train_model(
    train_rows: list[dict],
    test_rows: list[dict],
    *,
    calibrate: bool = True,
) -> TrainedModel:
    """Train a HistGradientBoostingClassifier and evaluate on test set.

    Args:
        train_rows: Training data (dicts from DB query with feature columns + outcome).
        test_rows: Test data (same format).
        calibrate: Whether to apply Platt calibration for better probabilities.

    Returns:
        TrainedModel with the fitted model, metrics, and feature importances.
    """
    all_features = FEATURE_COLUMNS + CATEGORICAL_FEATURES

    # Filter to labeled rows only (exclude "unknown")
    train_labeled = [r for r in train_rows if r.get("outcome") in ("failed", "trading")]
    test_labeled = [r for r in test_rows if r.get("outcome") in ("failed", "trading")]

    X_train = _build_feature_matrix(train_labeled, FEATURE_COLUMNS, CATEGORICAL_FEATURES)  # noqa: N806
    y_train = _build_target(train_labeled)
    X_test = _build_feature_matrix(test_labeled, FEATURE_COLUMNS, CATEGORICAL_FEATURES)  # noqa: N806
    y_test = _build_target(test_labeled)

    # Train
    clf = HistGradientBoostingClassifier(
        max_iter=200,
        max_depth=5,
        learning_rate=0.1,
        min_samples_leaf=20,
        random_state=42,
    )
    clf.fit(X_train, y_train)

    # Calibrate probabilities if requested and we have enough data
    if calibrate and len(train_labeled) >= 100:
        cal_clf = CalibratedClassifierCV(clf, cv=3, method="isotonic")
        cal_clf.fit(X_train, y_train)
        model = cal_clf
    else:
        model = clf

    # Evaluate
    y_pred_proba = model.predict_proba(X_test)[:, 1]  # P(failed)

    auc = roc_auc_score(y_test, y_pred_proba) if len(set(y_test)) > 1 else 0.5
    ece = compute_ece(y_test.tolist(), y_pred_proba.tolist())

    # Feature importances via permutation importance on test set
    perm = permutation_importance(
        clf, X_test, y_test, n_repeats=5, random_state=42, scoring="roc_auc",
    )
    importances = {f: float(v) for f, v in zip(all_features, perm.importances_mean)}

    return TrainedModel(
        model=model,
        feature_names=all_features,
        auc=auc,
        ece=ece,
        n_train=len(train_labeled),
        n_test=len(test_labeled),
        feature_importances=importances,
    )


def score_deals(
    model: TrainedModel,
    rows: list[dict],
) -> list[float]:
    """Score a list of deals using a trained model.

    Returns a list of scores (0-100) where higher = more likely to survive
    (i.e., LOWER probability of failure).
    """
    X = _build_feature_matrix(rows, FEATURE_COLUMNS, CATEGORICAL_FEATURES)  # noqa: N806
    p_fail = model.model.predict_proba(X)[:, 1]
    # Invert: high score = low failure probability = good deal
    scores = [(1.0 - p) * 100.0 for p in p_fail]
    return scores


def train_progress_model(
    train_rows: list[dict],
    test_rows: list[dict],
    train_labels: dict[str, int],
    test_labels: dict[str, int],
    *,
    calibrate: bool = True,
) -> TrainedModel | None:
    """Train a progress model predicting 18-24 month milestone achievement.

    Same architecture as the survival model but with a different target
    (progress label) and additional growth features.

    Args:
        train_rows: Training data rows with feature columns.
        test_rows: Test data rows.
        train_labels: Dict mapping company_id -> progress_label (0 or 1).
        test_labels: Dict mapping company_id -> progress_label (0 or 1).
        calibrate: Whether to apply isotonic calibration.

    Returns:
        TrainedModel with progress AUC, or None if insufficient data.
    """
    all_features = PROGRESS_FEATURE_COLUMNS + PROGRESS_CATEGORICAL_FEATURES

    # Filter to rows that have a progress label
    train_labeled = [r for r in train_rows if r.get("company_id") in train_labels]
    test_labeled = [r for r in test_rows if r.get("company_id") in test_labels]

    if len(train_labeled) < 30 or len(test_labeled) < 10:
        return None

    X_train = _build_feature_matrix(  # noqa: N806
        train_labeled, PROGRESS_FEATURE_COLUMNS, PROGRESS_CATEGORICAL_FEATURES,
    )
    y_train = np.array([train_labels[r["company_id"]] for r in train_labeled])

    X_test = _build_feature_matrix(  # noqa: N806
        test_labeled, PROGRESS_FEATURE_COLUMNS, PROGRESS_CATEGORICAL_FEATURES,
    )
    y_test = np.array([test_labels[r["company_id"]] for r in test_labeled])

    # Need both classes in train and test
    if len(set(y_train)) < 2 or len(set(y_test)) < 2:
        return None

    clf = HistGradientBoostingClassifier(
        max_iter=200,
        max_depth=5,
        learning_rate=0.1,
        min_samples_leaf=20,
        random_state=42,
    )
    clf.fit(X_train, y_train)

    if calibrate and len(train_labeled) >= 100:
        cal_clf = CalibratedClassifierCV(clf, cv=3, method="isotonic")
        cal_clf.fit(X_train, y_train)
        model = cal_clf
    else:
        model = clf

    y_pred_proba = model.predict_proba(X_test)[:, 1]  # P(progress)
    auc = roc_auc_score(y_test, y_pred_proba)
    ece = compute_ece(y_test.tolist(), y_pred_proba.tolist())

    perm = permutation_importance(
        clf, X_test, y_test, n_repeats=5, random_state=42, scoring="roc_auc",
    )
    importances = {f: float(v) for f, v in zip(all_features, perm.importances_mean)}

    return TrainedModel(
        model=model,
        feature_names=all_features,
        auc=auc,
        ece=ece,
        n_train=len(train_labeled),
        n_test=len(test_labeled),
        feature_importances=importances,
    )


def save_model(model: TrainedModel, path: Path) -> None:
    """Persist a trained model to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        pickle.dump(model, f)


def load_model(path: Path) -> TrainedModel:
    """Load a trained model from disk."""
    with open(path, "rb") as f:
        return pickle.load(f)  # noqa: S301
