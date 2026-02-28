"""Extract market-regime-family features from raw company records."""

from __future__ import annotations

from typing import Any


def extract_market_regime_features(record: dict) -> dict[str, Any]:
    """Extract market regime features from a company record.

    Args:
        record: Raw company/market data dict.

    Returns:
        Dict of {feature_name: value} for all market_regime-family features.
    """
    return {
        "interest_rate_regime": record.get("interest_rate_regime"),
        "equity_market_regime": record.get("equity_market_regime"),
        "ecf_quarterly_volume": record.get("ecf_quarterly_volume"),
    }
