"""Extract campaign-family features from raw company records."""

from __future__ import annotations

from typing import Any


def extract_campaign_features(record: dict) -> dict[str, Any]:
    """Extract campaign features from a company record.

    Computes derived features:
      - overfunding_ratio = amount_raised / funding_target (0.0 on div-by-zero)

    Args:
        record: Raw company/campaign data dict.

    Returns:
        Dict of {feature_name: value} for all campaign-family features.
    """
    funding_target = record.get("funding_target")
    amount_raised = record.get("amount_raised")

    # Compute overfunding ratio safely
    overfunding_ratio: float | None = None
    if funding_target is not None and amount_raised is not None:
        if funding_target > 0:
            overfunding_ratio = amount_raised / funding_target
        else:
            overfunding_ratio = 0.0

    return {
        "funding_target": funding_target,
        "amount_raised": amount_raised,
        "overfunding_ratio": overfunding_ratio,
        "equity_offered_pct": record.get("equity_offered_pct"),
        "pre_money_valuation": record.get("pre_money_valuation"),
        "investor_count": record.get("investor_count"),
        "funding_velocity_days": record.get("funding_velocity_days"),
        "eis_seis_eligible": record.get("eis_seis_eligible"),
        "platform": record.get("platform"),
    }
