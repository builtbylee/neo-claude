"""Tests for synthetic analyst pilot helper logic."""

from __future__ import annotations

import sys
from pathlib import Path

# Allow importing scripts modules as test targets.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from run_synthetic_analyst_pilot import (  # noqa: E402
    _has_actionable_context,
    _parse_json_response,
    _persona_fallback_from_context,
)


def test_parse_json_response_supports_wrapped_payload() -> None:
    wrapped = "```json\n{\"recommendation_class\":\"watch\",\"conviction\":52}\n```"
    parsed = _parse_json_response(wrapped)
    assert parsed["recommendation_class"] == "watch"
    assert parsed["conviction"] == 52


def test_has_actionable_context_false_with_empty_payload() -> None:
    context = {
        "company": {"name": "Example", "sector": None},
        "model_output": {"score": None},
        "deal_snapshot": {
            "funding_target": None,
            "amount_raised": None,
            "pre_money_valuation": None,
            "stage_bucket": None,
            "latest_round": {
                "amount_raised": None,
                "pre_money_valuation": None,
            },
        },
    }
    assert _has_actionable_context(context) is False


def test_has_actionable_context_true_with_stage_bucket() -> None:
    context = {
        "company": {"name": "Example", "sector": None},
        "model_output": {"score": None},
        "deal_snapshot": {"stage_bucket": "seed", "latest_round": {}},
    }
    assert _has_actionable_context(context) is True


def test_persona_fallback_yields_different_profiles() -> None:
    context = {
        "deal_snapshot": {
            "amount_raised": 250000,
            "had_revenue": True,
            "stage_bucket": "seed",
            "latest_round": {"amount_raised": 500000},
        },
    }
    cons = _persona_fallback_from_context("conservative", context)
    bal = _persona_fallback_from_context("balanced", context)
    aggr = _persona_fallback_from_context("aggressive", context)

    assert cons[0] in {"watch", "deep_diligence"}
    assert bal[0] in {"deep_diligence", "invest"}
    assert aggr[0] in {"deep_diligence", "invest"}
    assert aggr[1] >= bal[1] >= cons[1]
