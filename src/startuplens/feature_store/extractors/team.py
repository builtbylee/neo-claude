"""Extract team-family features from raw company records."""

from __future__ import annotations

from typing import Any


def extract_team_features(record: dict) -> dict[str, Any]:
    """Extract team features from a company record.

    Args:
        record: Raw company/team data dict.

    Returns:
        Dict of {feature_name: value} for all team-family features.
    """
    return {
        "founder_count": record.get("founder_count"),
        "domain_experience_years": record.get("domain_experience_years"),
        "prior_exits": record.get("prior_exits"),
        "accelerator_alumni": record.get("accelerator_alumni"),
    }
