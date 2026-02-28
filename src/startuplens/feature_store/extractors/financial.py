"""Extract financial-family features from raw company records."""

from __future__ import annotations

from typing import Any


def extract_financial_features(record: dict) -> dict[str, Any]:
    """Extract financial features from a company record.

    Computes derived features:
      - debt_to_asset_ratio = total_debt / total_assets (None on div-by-zero)

    Args:
        record: Raw company/financial data dict.

    Returns:
        Dict of {feature_name: value} for all financial-family features.
    """
    total_assets = record.get("total_assets")
    total_debt = record.get("total_debt")

    # Compute debt-to-asset ratio safely
    debt_to_asset_ratio: float | None = None
    if total_assets is not None and total_debt is not None:
        if total_assets > 0:
            debt_to_asset_ratio = total_debt / total_assets
        else:
            debt_to_asset_ratio = 0.0

    return {
        "total_assets": total_assets,
        "total_debt": total_debt,
        "debt_to_asset_ratio": debt_to_asset_ratio,
        "cash_position": record.get("cash_position"),
        "burn_rate_monthly": record.get("burn_rate_monthly"),
        "gross_margin": record.get("gross_margin"),
    }
