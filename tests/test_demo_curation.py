"""Tests for scripts/run_demo_curation.py."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from run_demo_curation import _evidence_rank, _memo_markdown  # noqa: E402


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
