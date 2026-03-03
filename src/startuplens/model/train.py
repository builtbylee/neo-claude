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

try:  # pragma: no cover - environment-dependent native dependency.
    from xgboost import XGBClassifier
except Exception:  # noqa: BLE001
    XGBClassifier = None

from startuplens.backtest.metrics import compute_ece

# Features used for training — must match training_features_wide columns.
# Ordered to match the matview query.
FEATURE_COLUMNS = [
    "company_age_months",
    "employee_count",
    "revenue_at_raise",
    "pre_revenue",
    "revenue_growth_rate",
    "total_prior_funding",
    "total_assets",
    "total_debt",
    "debt_to_asset_ratio",
    "cash_position",
    "burn_rate_monthly",
    "gross_margin",
    "funding_target",
    "amount_raised",
    "overfunding_ratio",
    "equity_offered_pct",
    "pre_money_valuation",
    "investor_count",
    "funding_velocity_days",
    "valuation_cap",
    "discount_rate",
    "liquidation_pref_multiple",
    "seniority_position",
    "charges_count",
    "director_disqualifications",
    "ecf_quarterly_volume",
    "data_source_count",
    "field_completeness_ratio",
]

# Categorical features encoded as strings → need ordinal encoding
CATEGORICAL_FEATURES = [
    "instrument_type",
    "platform",
    "country",
    "sector",
    "revenue_model_type",
    "company_status",
    "interest_rate_regime",
    "equity_market_regime",
    "liquidation_participation",
]

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
    model_name: str = "hgb"


class AveragedEnsemble:
    """Simple weighted-probability ensemble over binary classifiers."""

    def __init__(self, models: list[Any], weights: list[float]) -> None:
        if len(models) != len(weights):
            raise ValueError("models and weights length mismatch")
        total = sum(weights) or 1.0
        self.models = models
        self.weights = [w / total for w in weights]

    def predict_proba(self, X: np.ndarray) -> np.ndarray:  # noqa: N803
        weighted = np.zeros((X.shape[0], 2), dtype=float)
        for model, weight in zip(self.models, self.weights):
            weighted += weight * model.predict_proba(X)
        return weighted


def _build_feature_matrix(
    rows: list[dict],
    feature_columns: list[str],
    categorical_columns: list[str],
) -> np.ndarray:
    """Convert list of dicts into a numpy feature matrix.

    Numeric features are passed through. For sparse wide schemas, missing numeric
    values default to 0.0 when the row has at least one observed signal, while
    fully-empty rows remain all-NaN (degenerate case detection).
    Categorical features are hashed to integers.
    """
    all_cols = feature_columns + categorical_columns
    n_rows = len(rows)
    n_cols = len(all_cols)
    X = np.full((n_rows, n_cols), np.nan)  # noqa: N806

    for i, row in enumerate(rows):
        has_any_signal = any(row.get(col) is not None for col in all_cols)
        for j, col in enumerate(all_cols):
            val = row.get(col)
            if val is None:
                if has_any_signal and col in feature_columns:
                    # Sparse numeric families are common; zero-fill avoids
                    # collapsing mostly-populated rows into near-all-NaN.
                    X[i, j] = 0.0
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


def _compute_sample_weights(y: np.ndarray) -> np.ndarray:
    """Inverse-frequency sample weights with bounded ratio for calibration stability."""
    positives = max(int(np.sum(y == 1)), 1)
    negatives = max(int(np.sum(y == 0)), 1)
    pos_w = min(negatives / positives, 10.0)
    neg_w = min(positives / negatives, 10.0)
    return np.where(y == 1, pos_w, neg_w).astype(float)


def _calibrate_classifier(
    clf: Any,
    X_train: np.ndarray,  # noqa: N803
    y_train: np.ndarray,
    strategy: str = "auto",
) -> tuple[Any, str]:
    """Calibrate with Platt (sigmoid) by default, isotonic only when sample is large."""
    if strategy == "none":
        return clf, "none"
    method = "sigmoid"
    if strategy == "isotonic":
        method = "isotonic"
    elif strategy == "auto" and len(y_train) >= 500:
        method = "isotonic"
    calibrated = CalibratedClassifierCV(clf, cv=3, method=method)
    calibrated.fit(X_train, y_train)
    return calibrated, method


def train_model(
    train_rows: list[dict],
    test_rows: list[dict],
    *,
    calibrate: bool = True,
    calibration_strategy: str = "auto",
    allow_challenger: bool = True,
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
    sample_weights = _compute_sample_weights(y_train)

    hgb = HistGradientBoostingClassifier(
        max_iter=200,
        max_depth=5,
        learning_rate=0.1,
        min_samples_leaf=20,
        random_state=42,
    )
    hgb.fit(X_train, y_train, sample_weight=sample_weights)

    xgb = None
    if allow_challenger and XGBClassifier is not None:
        xgb = XGBClassifier(
            objective="binary:logistic",
            n_estimators=250,
            max_depth=5,
            learning_rate=0.06,
            subsample=0.9,
            colsample_bytree=0.9,
            reg_lambda=1.0,
            min_child_weight=5,
            eval_metric="logloss",
            random_state=42,
        )
        xgb.fit(X_train, y_train, sample_weight=sample_weights)

    # Calibrate probabilities if requested and we have enough data
    if calibrate and len(train_labeled) >= 120:
        hgb_model, _ = _calibrate_classifier(hgb, X_train, y_train, calibration_strategy)
        if xgb is not None:
            xgb_model, _ = _calibrate_classifier(xgb, X_train, y_train, calibration_strategy)
        else:
            xgb_model = None
    else:
        hgb_model = hgb
        xgb_model = xgb

    # Evaluate
    hgb_pred = hgb_model.predict_proba(X_test)[:, 1]
    if len(set(y_test)) > 1:
        hgb_auc = roc_auc_score(y_test, hgb_pred)
    else:
        hgb_auc = 0.5
    candidates = [("hgb", hgb_model, hgb_pred, hgb_auc)]

    if xgb_model is not None:
        xgb_pred = xgb_model.predict_proba(X_test)[:, 1]
        xgb_auc = roc_auc_score(y_test, xgb_pred) if len(set(y_test)) > 1 else 0.5
        candidates.append(("xgb", xgb_model, xgb_pred, xgb_auc))
        ensemble = AveragedEnsemble([hgb_model, xgb_model], [0.5, 0.5])
        ens_pred = ensemble.predict_proba(X_test)[:, 1]
        ens_auc = roc_auc_score(y_test, ens_pred) if len(set(y_test)) > 1 else 0.5
        candidates.append(("ensemble", ensemble, ens_pred, ens_auc))
    best_name, best_model, best_pred, best_auc = max(candidates, key=lambda t: t[3])
    auc = best_auc
    ece = compute_ece(y_test.tolist(), best_pred.tolist())

    # Feature importances via permutation importance on test set
    perm = permutation_importance(
        hgb, X_test, y_test, n_repeats=5, random_state=42, scoring="roc_auc",
    )
    importances = {f: float(v) for f, v in zip(all_features, perm.importances_mean)}

    return TrainedModel(
        model=best_model,
        feature_names=all_features,
        auc=auc,
        ece=ece,
        n_train=len(train_labeled),
        n_test=len(test_labeled),
        feature_importances=importances,
        model_name=best_name,
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


def predict_failure_probabilities(
    model: TrainedModel,
    rows: list[dict],
) -> list[float]:
    """Return raw P(fail) probabilities for calibration/evidence diagnostics."""
    X = _build_feature_matrix(rows, FEATURE_COLUMNS, CATEGORICAL_FEATURES)  # noqa: N806
    p_fail = model.model.predict_proba(X)[:, 1]
    return [float(p) for p in p_fail]


def train_progress_model(
    train_rows: list[dict],
    test_rows: list[dict],
    train_labels: dict[str, int],
    test_labels: dict[str, int],
    *,
    calibrate: bool = True,
    calibration_strategy: str = "auto",
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

    sample_weights = _compute_sample_weights(y_train)
    clf = HistGradientBoostingClassifier(
        max_iter=200,
        max_depth=5,
        learning_rate=0.1,
        min_samples_leaf=20,
        random_state=42,
    )
    clf.fit(X_train, y_train, sample_weight=sample_weights)

    if calibrate and len(train_labeled) >= 100:
        model, _ = _calibrate_classifier(clf, X_train, y_train, calibration_strategy)
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
        model_name="hgb_progress",
    )


def filter_rows_for_family(rows: list[dict], family: str) -> list[dict]:
    """Filter rows for stage-country family with pooled fallback handled by caller."""
    family_map = {
        "UK_Seed": ("UK", "seed"),
        "UK_EarlyGrowth": ("UK", "early_growth"),
        "US_Seed": ("US", "seed"),
        "US_EarlyGrowth": ("US", "early_growth"),
    }
    if family not in family_map:
        return rows
    country, stage = family_map[family]
    filtered = [
        r
        for r in rows
        if (r.get("country") or "").upper() == country
        and (r.get("stage_bucket") or "").lower() == stage
    ]
    return filtered


def save_model(model: TrainedModel, path: Path) -> None:
    """Persist a trained model to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        pickle.dump(model, f)


def load_model(path: Path) -> TrainedModel:
    """Load a trained model from disk."""
    with open(path, "rb") as f:
        return pickle.load(f)  # noqa: S301
