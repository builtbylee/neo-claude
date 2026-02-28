"""Extract company-family features from raw company records."""

from __future__ import annotations

from datetime import date
from typing import Any


def _compute_age_months(
    incorporation_date: date | str | None,
    campaign_date: date | str | None,
) -> int | None:
    """Compute company age in months between incorporation and campaign dates.

    Accepts date objects or ISO-format strings (YYYY-MM-DD).
    Returns None if either date is missing.
    """
    if incorporation_date is None or campaign_date is None:
        return None

    if isinstance(incorporation_date, str):
        incorporation_date = date.fromisoformat(incorporation_date)
    if isinstance(campaign_date, str):
        campaign_date = date.fromisoformat(campaign_date)

    delta_days = (campaign_date - incorporation_date).days
    if delta_days < 0:
        return 0
    return delta_days // 30


def extract_company_features(record: dict) -> dict[str, Any]:
    """Extract company features from a company record.

    Computes derived features:
      - company_age_months from incorporation_date and campaign_date
      - pre_revenue = True if revenue_at_raise is None or 0

    Args:
        record: Raw company data dict.

    Returns:
        Dict of {feature_name: value} for all company-family features.
    """
    revenue_at_raise = record.get("revenue_at_raise")
    pre_revenue = revenue_at_raise is None or revenue_at_raise == 0

    company_age_months = _compute_age_months(
        record.get("incorporation_date"),
        record.get("campaign_date"),
    )

    return {
        "company_age_months": company_age_months,
        "employee_count": record.get("employee_count"),
        "revenue_at_raise": revenue_at_raise,
        "pre_revenue": pre_revenue,
        "revenue_growth_rate": record.get("revenue_growth_rate"),
        "total_prior_funding": record.get("total_prior_funding"),
        "prior_vc_backing": record.get("prior_vc_backing"),
        "sector": record.get("sector"),
        "revenue_model_type": record.get("revenue_model_type"),
        "country": record.get("country"),
    }
