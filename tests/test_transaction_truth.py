from __future__ import annotations

from datetime import date

from startuplens.pipelines.transaction_truth import (
    _coerce_fact_value,
    _extract_edgar_terms,
    _is_conflict,
    build_round_stitch_key,
)


def test_build_round_stitch_key_is_stable() -> None:
    key = build_round_stitch_key(
        company_id="abc",
        round_date=date(2025, 1, 15),
        round_type="Series A",
        instrument_type="equity",
        amount_raised=12_500_000,
    )
    assert key == "abc:2025-01-15:series a:equity:12500000.00"


def test_extract_edgar_terms_parses_common_patterns() -> None:
    text = """
    The Notes include a 20% discount and a valuation cap of $12000000.
    Investors receive a 1x liquidation preference and pro rata rights.
    """
    terms = _extract_edgar_terms(text)

    assert terms["discount_rate"] == 0.2
    assert terms["valuation_cap"] == 12_000_000
    assert terms["liquidation_preference_multiple"] == 1.0
    assert terms["pro_rata_rights"] is True


def test_coerce_fact_value_handles_types() -> None:
    assert _coerce_fact_value("discount_rate", "20%") == 0.2
    assert _coerce_fact_value("pro_rata_rights", "yes") is True
    assert _coerce_fact_value("maturity_date", "2026-01-01") == "2026-01-01"


def test_is_conflict_respects_numeric_tolerance() -> None:
    assert _is_conflict(100.0, 102.0) is False
    assert _is_conflict(100.0, 120.0) is True
    assert _is_conflict("safe", "SAFE") is False
    assert _is_conflict("safe", "equity") is True
