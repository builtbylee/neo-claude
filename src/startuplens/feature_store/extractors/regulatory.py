"""Extract regulatory-family features from raw company records.

Derives compliance signals from Companies House data: company status,
accounts overdue, charges, and director disqualifications.
"""

from __future__ import annotations

from typing import Any


def extract_regulatory_features(record: dict) -> dict[str, Any]:
    """Extract regulatory features from a company record.

    Handles both Companies House normalized records (which have
    direct fields) and generic records that may have partial data.

    Args:
        record: Raw company/regulatory data dict.

    Returns:
        Dict of {feature_name: value} for all regulatory-family features.
    """
    # Company status: active, dissolved, liquidation, etc.
    status = record.get("company_status") or record.get("current_status")

    # Accounts overdue flag
    accounts_overdue = record.get("accounts_overdue")

    # Charges count (secured debts / mortgages)
    charges_count = record.get("charges_count")
    if charges_count is None and record.get("has_charges"):
        charges_count = 1  # At least one charge if has_charges is True

    # Director disqualifications
    disqualifications = record.get("director_disqualifications")

    return {
        "company_status": status,
        "accounts_overdue": accounts_overdue,
        "charges_count": charges_count,
        "director_disqualifications": disqualifications,
    }
