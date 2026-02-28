"""Extract terms/pricing-family features from raw company records."""

from __future__ import annotations

from typing import Any


def extract_terms_features(record: dict) -> dict[str, Any]:
    """Extract investment terms features from a company record.

    Args:
        record: Raw company/terms data dict.

    Returns:
        Dict of {feature_name: value} for all terms-family features.
    """
    return {
        "instrument_type": record.get("instrument_type"),
        "valuation_cap": record.get("valuation_cap"),
        "discount_rate": record.get("discount_rate"),
        "mfn_clause": record.get("mfn_clause"),
        "liquidation_pref_multiple": record.get("liquidation_pref_multiple"),
        "liquidation_participation": record.get("liquidation_participation"),
        "seniority_position": record.get("seniority_position"),
        "pro_rata_rights": record.get("pro_rata_rights"),
        "qualified_institutional": record.get("qualified_institutional"),
    }
