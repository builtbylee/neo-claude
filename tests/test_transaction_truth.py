from __future__ import annotations

import io
import zipfile
from datetime import date

import pandas as pd

from startuplens.pipelines.transaction_truth import (
    _coerce_fact_value,
    _extract_adv_latest_zip_links,
    _extract_edgar_terms,
    _extract_terms_from_form_c_text,
    _is_conflict,
    _parse_adv_rows_from_zip,
    _parse_scaled_amount,
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


def test_parse_scaled_amount_handles_units() -> None:
    assert _parse_scaled_amount("2m") == 2_000_000
    assert _parse_scaled_amount("12.5 million") == 12_500_000
    assert _parse_scaled_amount("750k") == 750_000
    assert _parse_scaled_amount("1200000") == 1_200_000


def test_extract_terms_from_form_c_text_parses_key_fields() -> None:
    text = """
    We are raising on a SAFE with a valuation cap of $10 million and a 20% discount.
    The pre-money valuation is $8,000,000 and post-money valuation is $10,000,000.
    Investors have pro rata rights and 1x liquidation preference.
    This round is led by Example Ventures.
    """
    terms = _extract_terms_from_form_c_text(text)
    assert terms["valuation_cap"] == 10_000_000
    assert terms["discount_rate"] == 0.2
    assert terms["pre_money_valuation"] == 8_000_000
    assert terms["post_money_valuation"] == 10_000_000
    assert terms["liquidation_preference_multiple"] == 1.0
    assert terms["pro_rata_rights"] is True
    assert terms["lead_investor"] == "Example Ventures"


def test_extract_terms_from_form_c_text_handles_negative_pro_rata() -> None:
    text = "No pro rata rights are granted to noteholders."
    terms = _extract_terms_from_form_c_text(text)
    assert terms["pro_rata_rights"] is False


def test_coerce_fact_value_handles_types() -> None:
    assert _coerce_fact_value("discount_rate", "20%") == 0.2
    assert _coerce_fact_value("pro_rata_rights", "yes") is True
    assert _coerce_fact_value("maturity_date", "2026-01-01") == "2026-01-01"


def test_is_conflict_respects_numeric_tolerance() -> None:
    assert _is_conflict(100.0, 102.0) is False
    assert _is_conflict(100.0, 120.0) is True
    assert _is_conflict("safe", "SAFE") is False
    assert _is_conflict("safe", "equity") is True


def test_extract_adv_latest_zip_links_sorts_by_embedded_date() -> None:
    html = """
    <a href="/files/node/add/data_distribution/ia010120.zip">old</a>
    <a href="/files/node/add/data_distribution/ia123123.zip">new</a>
    <a href="/files/node/add/data_distribution/ia020120-exempt.zip">mid</a>
    <a href="/files/node/add/data_distribution/not-adv.zip">skip</a>
    """
    links = _extract_adv_latest_zip_links(html)
    assert links[0].endswith("ia123123.zip")
    assert links[1].endswith("ia020120-exempt.zip")
    assert links[2].endswith("ia010120.zip")


def test_parse_adv_rows_from_zip_handles_xlsx_payload() -> None:
    df = pd.DataFrame(
        [
            {
                "Legal_Name": "Example Capital LLC",
                "CRD_Number": "12345",
                "Regulatory_Assets_Under_Management": 1_250_000_000,
            }
        ]
    )
    excel_buffer = io.BytesIO()
    with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("ia010120.xlsx", excel_buffer.getvalue())

    rows = _parse_adv_rows_from_zip(zip_buffer.getvalue())
    assert len(rows) == 1
    assert rows[0]["legal_name"] == "Example Capital LLC"
    assert str(rows[0]["crd_number"]) == "12345"
