"""Extract regulatory-family features from raw company records."""

from __future__ import annotations

from typing import Any


def extract_regulatory_features(record: dict) -> dict[str, Any]:
    """Extract regulatory features from a company record.

    Args:
        record: Raw company/regulatory data dict.

    Returns:
        Dict of {feature_name: value} for all regulatory-family features.
    """
    return {
        "company_status": record.get("company_status"),
        "accounts_overdue": record.get("accounts_overdue"),
        "charges_count": record.get("charges_count"),
        "director_disqualifications": record.get("director_disqualifications"),
    }
