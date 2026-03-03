"""Extract evidence-quality features from raw records."""

from __future__ import annotations

from typing import Any


def extract_evidence_features(record: dict) -> dict[str, Any]:
    """Compute lightweight evidence quality metrics for confidence gating."""
    source_flags = [
        bool(record.get("source")),
        bool(record.get("campaign_date")),
        bool(record.get("round_date")),
        bool(record.get("total_assets") is not None),
        bool(record.get("company_status")),
    ]
    data_source_count = sum(1 for v in source_flags if v)

    tracked_fields = [
        "funding_target",
        "amount_raised",
        "pre_money_valuation",
        "investor_count",
        "employee_count",
        "revenue_at_raise",
        "total_assets",
        "total_debt",
        "instrument_type",
        "company_status",
        "country",
        "sector",
    ]
    present = sum(1 for field in tracked_fields if record.get(field) is not None)
    field_completeness_ratio = present / len(tracked_fields)

    return {
        "data_source_count": data_source_count,
        "field_completeness_ratio": field_completeness_ratio,
    }

