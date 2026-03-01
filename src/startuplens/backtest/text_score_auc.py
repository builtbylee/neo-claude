"""AUC computation for Claude text scores against crowdfunding outcomes.

Provides standalone functions to compute ROC AUC for the aggregate
text_quality_score and per-dimension AUCs. Results feed into
evaluate_backtest(claude_text_score_auc=...) in metrics.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from sklearn.metrics import roc_auc_score

if TYPE_CHECKING:
    import psycopg

logger = structlog.get_logger(__name__)


def compute_claude_text_auc(conn: psycopg.Connection) -> float:
    """Compute ROC AUC of text_quality_score against binary outcome.

    Binary label:
        outcome IN ('trading', 'exited') → 1 (positive/survived)
        outcome = 'failed' → 0

    Returns AUC as a float, or 0.0 if insufficient data.
    """
    from startuplens.db import execute_query

    rows = execute_query(
        conn,
        """
        SELECT
            s.text_quality_score,
            co.outcome
        FROM claude_text_scores s
        JOIN crowdfunding_outcomes co ON co.company_id = s.company_id
        WHERE co.outcome IN ('trading', 'exited', 'failed')
          AND co.label_quality_tier <= 2
        """,
    )

    if len(rows) < 10:
        logger.warning("insufficient_scored_companies", count=len(rows))
        return 0.0

    y_true = [1 if r["outcome"] in ("trading", "exited") else 0 for r in rows]
    y_pred = [r["text_quality_score"] / 100.0 for r in rows]

    # Check we have both classes
    if len(set(y_true)) < 2:
        logger.warning("single_class_only", positive=sum(y_true), total=len(y_true))
        return 0.0

    auc = roc_auc_score(y_true, y_pred)

    n_positive = sum(y_true)
    n_negative = len(y_true) - n_positive
    logger.info(
        "claude_text_auc_computed",
        auc=round(auc, 4),
        n_scored=len(rows),
        n_positive=n_positive,
        n_negative=n_negative,
    )

    return float(auc)


def compute_dimension_aucs(conn: psycopg.Connection) -> dict[str, float]:
    """Compute per-dimension AUCs to identify which dimensions discriminate best.

    Returns a dict mapping dimension name to its AUC.
    """
    from startuplens.db import execute_query

    dimensions = (
        "clarity",
        "claims_plausibility",
        "problem_specificity",
        "differentiation_depth",
        "founder_domain_signal",
        "risk_honesty",
        "business_model_clarity",
        "text_quality_score",
    )

    rows = execute_query(
        conn,
        """
        SELECT
            s.clarity, s.claims_plausibility, s.problem_specificity,
            s.differentiation_depth, s.founder_domain_signal,
            s.risk_honesty, s.business_model_clarity, s.text_quality_score,
            co.outcome
        FROM claude_text_scores s
        JOIN crowdfunding_outcomes co ON co.company_id = s.company_id
        WHERE co.outcome IN ('trading', 'exited', 'failed')
          AND co.label_quality_tier <= 2
        """,
    )

    if len(rows) < 10:
        logger.warning("insufficient_scored_companies", count=len(rows))
        return {dim: 0.0 for dim in dimensions}

    y_true = [1 if r["outcome"] in ("trading", "exited") else 0 for r in rows]

    if len(set(y_true)) < 2:
        return {dim: 0.0 for dim in dimensions}

    result: dict[str, float] = {}
    for dim in dimensions:
        y_pred = [r[dim] / 100.0 for r in rows]
        auc = roc_auc_score(y_true, y_pred)
        result[dim] = round(float(auc), 4)

    logger.info("dimension_aucs_computed", aucs=result)
    return result
