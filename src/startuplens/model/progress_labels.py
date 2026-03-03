"""Progress label construction for the 18-24 month milestone model.

Labels companies as progressed (1) or not progressed (0) based on:
  (a) Follow-on Form C filing within 6-24 months of campaign, OR
  (b) Revenue >= 2x revenue_at_raise from a subsequent filing

Only labels companies where 18+ months have elapsed since campaign_date
to avoid right-censoring bias.
"""

from __future__ import annotations

import structlog

from startuplens.db import execute_query

logger = structlog.get_logger(__name__)

_PROGRESS_LABEL_QUERY = """
WITH campaign_base AS (
    SELECT DISTINCT ON (co.company_id)
        co.company_id,
        c.id AS company_id_fk,
        co.campaign_date,
        co.revenue_at_raise,
        SPLIT_PART(c.source_id, '_q', 1) AS cik
    FROM crowdfunding_outcomes co
    JOIN companies c ON c.id = co.company_id
    WHERE c.source = 'sec_dera_cf'
      AND co.label_quality_tier <= 2
      AND co.outcome IN ('failed', 'trading')
      AND co.campaign_date IS NOT NULL
      AND co.campaign_date BETWEEN %s AND %s
      AND co.campaign_date <= %s::date - INTERVAL '18 months'
    ORDER BY co.company_id, co.campaign_date
),
follow_on AS (
    SELECT DISTINCT cb.company_id, true AS has_follow_on
    FROM campaign_base cb
    JOIN sec_cf_filings scf
        ON scf.cik = cb.cik
        AND scf.filing_date > cb.campaign_date + INTERVAL '6 months'
        AND scf.filing_date <= LEAST(
            cb.campaign_date + INTERVAL '24 months', %s::date
        )
        AND scf.submission_type = 'C'
),
revenue_progress AS (
    SELECT DISTINCT cb.company_id, true AS has_revenue_2x
    FROM campaign_base cb
    JOIN companies c2
        ON c2.source = 'sec_dera_cf'
        AND SPLIT_PART(c2.source_id, '_q', 1) = cb.cik
    JOIN financial_data fd2
        ON fd2.company_id = c2.id
        AND fd2.period_type = 'annual'
        AND fd2.period_end_date > cb.campaign_date + INTERVAL '6 months'
        AND fd2.period_end_date <= LEAST(
            cb.campaign_date + INTERVAL '24 months', %s::date
        )
    WHERE cb.revenue_at_raise IS NOT NULL
      AND cb.revenue_at_raise > 0
      AND fd2.revenue >= cb.revenue_at_raise * 2
)
SELECT
    cb.company_id::text,
    CASE
        WHEN fo.has_follow_on IS TRUE OR rp.has_revenue_2x IS TRUE THEN 1
        ELSE 0
    END AS progress_label
FROM campaign_base cb
LEFT JOIN follow_on fo ON fo.company_id = cb.company_id
LEFT JOIN revenue_progress rp ON rp.company_id = cb.company_id
"""


def load_progress_labels(
    conn,
    start: str,
    end: str,
    cutoff_date: str | None = None,
) -> dict[str, int]:
    """Load progress labels for companies with campaigns in [start, end].

    Parameters
    ----------
    cutoff_date:
        Evaluation date for the 18-month maturity filter. Defaults to *end*
        if not provided, ensuring reproducible labels per backtest window.

    Returns dict mapping company_id (UUID str) -> progress_label (0 or 1).
    """
    if cutoff_date is None:
        cutoff_date = end
    rows = execute_query(
        conn, _PROGRESS_LABEL_QUERY,
        (start, end, cutoff_date, cutoff_date, cutoff_date),
    )
    labels = {r["company_id"]: r["progress_label"] for r in rows}
    n_pos = sum(1 for v in labels.values() if v == 1)
    logger.info(
        "loaded_progress_labels",
        total=len(labels),
        positive=n_pos,
        negative=len(labels) - n_pos,
        date_range=f"{start} to {end}",
    )
    return labels
