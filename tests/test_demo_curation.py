"""Tests for scripts/run_demo_curation.py."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from run_demo_curation import _evidence_rank, _memo_markdown, _select_diverse_items  # noqa: E402


def test_evidence_rank_prefers_higher_sufficiency() -> None:
    low = {"data_sufficiency": {"score": 55, "category_count": 3}, "model_recommendation": None}
    high = {"data_sufficiency": {"score": 80, "category_count": 4}, "model_recommendation": "watch"}
    assert _evidence_rank(high) > _evidence_rank(low)


def test_memo_markdown_contains_required_sections() -> None:
    item = {
        "company_name": "Acme Labs",
        "model_recommendation": "deep_diligence",
        "score": 62,
        "data_sufficiency": {"score": 78, "category_count": 4},
        "category_scores": {"Deal Terms": 58},
        "risk_flags": ["burn rate"],
        "missing_data_fields": ["customer_count"],
        "valuation_analysis": {"entry_multiple": 15},
        "instrument_type": "safe",
        "amount_raised": 400000,
        "pre_money_valuation": 4000000,
        "cf_investor_count": 120,
        "sector": "technology",
        "country": "US",
    }
    memo = _memo_markdown(item)
    assert "Demo Memo: Acme Labs" in memo
    assert "## Terms Snapshot" in memo
    assert "## Category Scores" in memo
    assert "## Risks" in memo
    assert "## Missing Data" in memo


def test_select_diverse_items_prefers_class_diversity() -> None:
    items = [
        {
            "shadow_cycle_item_id": "i1",
            "company_name": "A",
            "model_recommendation": "watch",
            "sector": "fintech",
            "country": "US",
            "data_sufficiency": {"score": 90, "category_count": 5},
        },
        {
            "shadow_cycle_item_id": "i2",
            "company_name": "B",
            "model_recommendation": "watch",
            "sector": "fintech",
            "country": "US",
            "data_sufficiency": {"score": 89, "category_count": 5},
        },
        {
            "shadow_cycle_item_id": "i3",
            "company_name": "C",
            "model_recommendation": "deep_diligence",
            "sector": "health",
            "country": "UK",
            "data_sufficiency": {"score": 75, "category_count": 4},
        },
        {
            "shadow_cycle_item_id": "i4",
            "company_name": "D",
            "model_recommendation": "pass",
            "sector": "climate",
            "country": "US",
            "data_sufficiency": {"score": 70, "category_count": 4},
        },
    ]
    selected = _select_diverse_items(items, top_n=3)
    classes = {str(i.get("model_recommendation")).lower() for i in selected}
    assert len(selected) == 3
    assert len(classes) >= 3
