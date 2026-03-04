"""Canonical transaction truth model and free-source spine builders.

This module builds one canonical row per financing round, stores field-level
facts from multiple public sources, reconciles conflicts, and computes
analyst-grade valuation confidence metrics.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
import time
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

import httpx
import pandas as pd
import structlog

from startuplens.db import execute_query

if TYPE_CHECKING:
    import psycopg

    from startuplens.config import Settings

logger = structlog.get_logger(__name__)

SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"
SEC_FORM_ADV_PAGE = (
    "https://www.sec.gov/data-research/sec-markets-data/"
    "information-about-registered-investment-advisers-exempt-reporting-advisers"
)
COMPANIES_HOUSE_BASE = "https://api.company-information.service.gov.uk"
UKRI_GTR_BASE = "https://gtr.ukri.org/gtr/api"
PATENTSVIEW_SEARCH = "https://api.patentsview.org/patents/query"
CONTRACTS_FINDER_SEARCH = "https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search"

_MIN_REQUEST_INTERVAL = 0.11
_CORE_TERM_FIELDS = (
    "round_type",
    "instrument_type",
    "amount_raised",
    "pre_money_valuation",
    "post_money_valuation",
    "valuation_cap",
    "discount_rate",
    "liquidation_preference_multiple",
    "liquidation_participation",
    "pro_rata_rights",
    "lead_investor",
)
_VALUATION_CRITICAL_FIELDS = {
    "round_type",
    "instrument_type",
    "pre_money_valuation",
    "post_money_valuation",
    "valuation_cap",
    "discount_rate",
    "liquidation_preference_multiple",
    "pro_rata_rights",
}
_NUMERIC_FIELDS = {
    "amount_raised",
    "pre_money_valuation",
    "post_money_valuation",
    "valuation_cap",
    "discount_rate",
    "interest_rate",
    "liquidation_preference_multiple",
    "arr_revenue",
    "revenue_growth_yoy",
    "burn_rate_monthly",
    "runway_months",
    "lead_investor_quality",
}
_BOOLEAN_FIELDS = {"pro_rata_rights", "valuation_gate_pass"}
_DATE_FIELDS = {"round_date", "maturity_date"}


@dataclass
class RoundFieldFact:
    transaction_round_id: str
    field_name: str
    field_value: Any
    source_name: str
    source_record_id: str | None
    source_tier: str
    as_of_timestamp: datetime | None


def _now_utc() -> datetime:
    return datetime.now(tz=UTC)


def _json_default(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _safe_json_dumps(value: Any) -> str:
    return json.dumps(value, default=_json_default)


def _normalize_name(value: str | None) -> str:
    if not value:
        return ""
    lowered = value.lower().strip()
    cleaned = re.sub(r"[^a-z0-9\s]", " ", lowered)
    cleaned = re.sub(
        r"\b(the|inc|llc|ltd|limited|plc|corp|corporation|partners|capital|ventures)\b",
        " ",
        cleaned,
    )
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        val = float(value)
        return val if math.isfinite(val) else None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    if text.endswith("%"):
        text = text[:-1]
        try:
            return float(text) / 100.0
        except ValueError:
            return None
    text = text.replace("$", "")
    try:
        return float(text)
    except ValueError:
        return None


def _safe_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "t", "yes", "y", "1"}:
        return True
    if text in {"false", "f", "no", "n", "0"}:
        return False
    return None


def _safe_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%d-%b-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _source_tier_for_name(source_name: str) -> str:
    s = source_name.lower()
    if any(
        k in s
        for k in (
            "sec_dera_cf",
            "sec_edgar",
            "companies_house",
            "sec_cf_filings",
            "form_d_dataset",
            "sh01",
        )
    ):
        return "A"
    if any(k in s for k in ("sec_form_d", "form_adv", "ukri", "contracts", "patentsview", "uspto")):
        return "B"
    return "C"


def _tier_rank(tier: str) -> int:
    return {"A": 3, "B": 2, "C": 1}.get(tier, 0)


def _rate_limited_get(
    client: httpx.Client, url: str, *, params: dict[str, Any] | None = None
) -> httpx.Response:
    time.sleep(_MIN_REQUEST_INTERVAL)
    resp = client.get(url, params=params)
    resp.raise_for_status()
    return resp


def build_round_stitch_key(
    *,
    company_id: str,
    round_date: date | None,
    round_type: str | None,
    instrument_type: str | None,
    amount_raised: float | None,
) -> str:
    rd = round_date.isoformat() if round_date else "unknown"
    rt = (round_type or "unknown").lower().strip()
    inst = (instrument_type or "unknown").lower().strip()
    amt = f"{amount_raised:.2f}" if amount_raised is not None else "unknown"
    return f"{company_id}:{rd}:{rt}:{inst}:{amt}"


def upsert_transaction_round(
    conn: psycopg.Connection,
    *,
    company_id: str,
    entity_id: str | None,
    country: str | None,
    sector: str | None,
    stage_bucket: str | None,
    round_stitch_key: str,
    round_type: str | None,
    instrument_type: str | None,
    round_date: date | None,
    amount_raised: float | None,
    source_timestamp: datetime | None,
    source_tier: str,
) -> str:
    row = execute_query(
        conn,
        """
        INSERT INTO transaction_rounds (
          company_id,
          entity_id,
          country,
          sector,
          stage_bucket,
          round_stitch_key,
          round_type,
          instrument_type,
          round_date,
          amount_raised,
          source_timestamp,
          source_tier,
          updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (round_stitch_key) DO UPDATE
        SET
          entity_id = COALESCE(EXCLUDED.entity_id, transaction_rounds.entity_id),
          country = COALESCE(EXCLUDED.country, transaction_rounds.country),
          sector = COALESCE(EXCLUDED.sector, transaction_rounds.sector),
          stage_bucket = COALESCE(EXCLUDED.stage_bucket, transaction_rounds.stage_bucket),
          round_type = COALESCE(EXCLUDED.round_type, transaction_rounds.round_type),
          instrument_type = COALESCE(EXCLUDED.instrument_type, transaction_rounds.instrument_type),
          round_date = COALESCE(EXCLUDED.round_date, transaction_rounds.round_date),
          amount_raised = COALESCE(EXCLUDED.amount_raised, transaction_rounds.amount_raised),
          source_timestamp = GREATEST(
            COALESCE(transaction_rounds.source_timestamp, EXCLUDED.source_timestamp),
            COALESCE(EXCLUDED.source_timestamp, transaction_rounds.source_timestamp)
          ),
          source_tier = CASE
            WHEN transaction_rounds.source_tier = 'A' OR EXCLUDED.source_tier = 'A' THEN 'A'
            WHEN transaction_rounds.source_tier = 'B' OR EXCLUDED.source_tier = 'B' THEN 'B'
            ELSE 'C'
          END,
          updated_at = now()
        RETURNING id
        """,
        (
            company_id,
            entity_id,
            country,
            sector,
            stage_bucket,
            round_stitch_key,
            round_type,
            instrument_type,
            round_date,
            amount_raised,
            source_timestamp,
            source_tier,
        ),
    )
    return row[0]["id"]


def insert_source_record(
    conn: psycopg.Connection,
    *,
    transaction_round_id: str,
    source_name: str,
    source_record_id: str | None,
    source_url: str | None,
    source_timestamp: datetime | None,
    source_tier: str,
    raw_payload: dict[str, Any] | None,
) -> None:
    execute_query(
        conn,
        """
        INSERT INTO transaction_round_source_records (
          transaction_round_id,
          source_name,
          source_record_id,
          source_url,
          source_timestamp,
          source_tier,
          raw_payload
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
        ON CONFLICT (transaction_round_id, source_name, source_record_id) DO UPDATE
        SET
          source_url = COALESCE(EXCLUDED.source_url, transaction_round_source_records.source_url),
          source_timestamp = GREATEST(
            COALESCE(transaction_round_source_records.source_timestamp, EXCLUDED.source_timestamp),
            COALESCE(EXCLUDED.source_timestamp, transaction_round_source_records.source_timestamp)
          ),
          source_tier = CASE
            WHEN transaction_round_source_records.source_tier = 'A'
              OR EXCLUDED.source_tier = 'A'
            THEN 'A'
            WHEN transaction_round_source_records.source_tier = 'B'
              OR EXCLUDED.source_tier = 'B'
            THEN 'B'
            ELSE 'C'
          END,
          raw_payload = transaction_round_source_records.raw_payload || EXCLUDED.raw_payload
        """,
        (
            transaction_round_id,
            source_name,
            source_record_id,
            source_url,
            source_timestamp,
            source_tier,
            _safe_json_dumps(raw_payload or {}),
        ),
    )


def insert_field_fact(conn: psycopg.Connection, fact: RoundFieldFact) -> None:
    execute_query(
        conn,
        """
        INSERT INTO transaction_round_field_facts (
          transaction_round_id,
          field_name,
          field_value,
          source_name,
          source_record_id,
          source_tier,
          as_of_timestamp
        )
        VALUES (%s, %s, %s::jsonb, %s, %s, %s, %s)
        """,
        (
            fact.transaction_round_id,
            fact.field_name,
            _safe_json_dumps({"value": fact.field_value}),
            fact.source_name,
            fact.source_record_id,
            fact.source_tier,
            fact.as_of_timestamp,
        ),
    )


def _stage_from_round(round_type: str | None, amount_raised: float | None) -> str | None:
    text = (round_type or "").lower()
    if "series a" in text or "series b" in text or "early" in text:
        return "early_growth"
    if "seed" in text or "pre-seed" in text:
        return "seed"
    if amount_raised is None:
        return None
    if amount_raised >= 2_000_000:
        return "early_growth"
    return "seed"


def _as_json_value(raw: dict[str, Any]) -> dict[str, Any]:
    return {k: _json_default(v) for k, v in raw.items() if v is not None}


def ingest_us_private_round_spine(
    conn: psycopg.Connection,
    *,
    country: str | None = "US",
    limit: int | None = None,
) -> dict[str, int]:
    """Build private-round spine from existing funding rounds.

    Includes amendment stitching by issuer/date/instrument/amount.
    """
    where_clauses = [
        "c.source IN ('sec_form_d', 'sec_edgar', 'sec_dera_cf', 'companies_house')",
    ]
    params: list[Any] = []
    if country:
        where_clauses.append("c.country = %s")
        params.append(country)

    query = f"""
        SELECT
          c.id AS company_id,
          c.entity_id,
          c.country,
          c.sector,
          c.source_id,
          fr.id AS funding_round_id,
          fr.round_date,
          fr.round_type,
          fr.instrument_type,
          fr.amount_raised,
          fr.pre_money_valuation,
          fr.post_money_valuation,
          fr.valuation_cap,
          fr.discount_rate,
          fr.interest_rate,
          fr.maturity_date,
          fr.liquidation_preference_multiple,
          fr.liquidation_participation,
          fr.pro_rata_rights,
          fr.lead_investor,
          fr.source,
          fd.revenue,
          fd.revenue_growth_yoy,
          fd.burn_rate_monthly,
          CASE
            WHEN fd.burn_rate_monthly IS NOT NULL AND fd.burn_rate_monthly > 0
                 AND fd.cash_and_equivalents IS NOT NULL
            THEN fd.cash_and_equivalents / fd.burn_rate_monthly
            ELSE NULL
          END AS runway_months,
          COALESCE(fr.round_date, c.created_at::date) AS as_of_date,
          c.created_at
        FROM funding_rounds fr
        JOIN companies c ON c.id = fr.company_id
        LEFT JOIN LATERAL (
            SELECT revenue, revenue_growth_yoy, burn_rate_monthly, cash_and_equivalents
            FROM financial_data f
            WHERE f.company_id = c.id
              AND (fr.round_date IS NULL OR f.period_end_date <= fr.round_date)
            ORDER BY f.period_end_date DESC
            LIMIT 1
        ) fd ON true
        WHERE {' AND '.join(where_clauses)}
        ORDER BY fr.round_date DESC NULLS LAST, fr.id
    """
    if limit is not None:
        query += " LIMIT %s"
        params.append(limit)

    rows = execute_query(conn, query, tuple(params))
    created = 0
    facts_inserted = 0

    for row in rows:
        round_date = row.get("round_date")
        if isinstance(round_date, datetime):
            round_date = round_date.date()
        amount = _safe_float(row.get("amount_raised"))
        stage_bucket = _stage_from_round(row.get("round_type"), amount)

        round_key = build_round_stitch_key(
            company_id=row["company_id"],
            round_date=round_date,
            round_type=row.get("round_type"),
            instrument_type=row.get("instrument_type"),
            amount_raised=amount,
        )

        round_id = upsert_transaction_round(
            conn,
            company_id=row["company_id"],
            entity_id=row.get("entity_id"),
            country=row.get("country"),
            sector=row.get("sector"),
            stage_bucket=stage_bucket,
            round_stitch_key=round_key,
            round_type=row.get("round_type"),
            instrument_type=row.get("instrument_type"),
            round_date=round_date,
            amount_raised=amount,
            source_timestamp=_now_utc(),
            source_tier=_source_tier_for_name(str(row.get("source") or "sec_form_d")),
        )
        created += 1

        source_id = str(row.get("funding_round_id"))
        source_name = str(row.get("source") or "funding_rounds")
        tier = _source_tier_for_name(source_name)

        insert_source_record(
            conn,
            transaction_round_id=round_id,
            source_name=source_name,
            source_record_id=source_id,
            source_url=None,
            source_timestamp=_now_utc(),
            source_tier=tier,
            raw_payload=_as_json_value(row),
        )

        for field in (
            "round_type",
            "instrument_type",
            "round_date",
            "amount_raised",
            "pre_money_valuation",
            "post_money_valuation",
            "valuation_cap",
            "discount_rate",
            "interest_rate",
            "maturity_date",
            "liquidation_preference_multiple",
            "liquidation_participation",
            "pro_rata_rights",
            "lead_investor",
            "arr_revenue",
            "revenue_growth_yoy",
            "burn_rate_monthly",
            "runway_months",
        ):
            value = row.get(field)
            if field == "arr_revenue":
                value = row.get("revenue")
            if value is None:
                continue
            insert_field_fact(
                conn,
                RoundFieldFact(
                    transaction_round_id=round_id,
                    field_name=field,
                    field_value=value,
                    source_name=source_name,
                    source_record_id=source_id,
                    source_tier=tier,
                    as_of_timestamp=datetime.combine(
                        round_date or date.today(),
                        datetime.min.time(),
                        tzinfo=UTC,
                    ),
                ),
            )
            facts_inserted += 1

    conn.commit()
    return {"rounds_upserted": created, "facts_inserted": facts_inserted}


def ingest_round_spine_from_crowdfunding_outcomes(
    conn: psycopg.Connection,
    *,
    max_label_tier: int = 3,
    limit: int | None = None,
) -> dict[str, int]:
    """Backfill transaction rounds directly from crowdfunding outcomes.

    This widens valuation/comparable coverage for cohorts that have campaign
    evidence but sparse funding_rounds rows.
    """
    query = """
        SELECT
          co.id::text AS outcome_id,
          co.company_id::text AS company_id,
          c.entity_id::text AS entity_id,
          c.country,
          COALESCE(co.sector, c.sector) AS sector,
          co.stage_bucket,
          co.campaign_date AS round_date,
          co.amount_raised,
          co.funding_target,
          co.pre_money_valuation,
          co.equity_offered,
          co.overfunding_ratio,
          co.investor_count,
          co.revenue_at_raise,
          co.data_source,
          co.label_quality_tier
        FROM crowdfunding_outcomes co
        JOIN companies c ON c.id = co.company_id
        WHERE co.label_quality_tier <= %s
          AND co.campaign_date IS NOT NULL
          AND (co.amount_raised IS NOT NULL OR co.funding_target IS NOT NULL)
        ORDER BY co.campaign_date DESC, co.id
    """
    params: list[Any] = [max_label_tier]
    if limit is not None:
        query += " LIMIT %s"
        params.append(limit)

    rows = execute_query(conn, query, tuple(params))

    rounds_upserted = 0
    facts_inserted = 0
    derived_terms = 0

    for row in rows:
        amount = _safe_float(row.get("amount_raised")) or _safe_float(row.get("funding_target"))
        round_date = row.get("round_date")
        if isinstance(round_date, datetime):
            round_date = round_date.date()

        data_source = str(row.get("data_source") or "")
        tier = _source_tier_for_name(data_source)
        if int(row.get("label_quality_tier") or 3) >= 3 and tier == "A":
            tier = "B"

        pre_money = _safe_float(row.get("pre_money_valuation"))
        equity_offered = _safe_float(row.get("equity_offered"))
        post_money = None
        if pre_money is not None and equity_offered is not None and 0 < equity_offered < 1:
            post_money = pre_money / max(1e-9, (1 - equity_offered))
            derived_terms += 1
        elif (
            pre_money is None
            and amount is not None
            and equity_offered is not None
            and 0 < equity_offered <= 1
        ):
            post_money = amount / max(1e-9, equity_offered)
            pre_money = max(0.0, post_money - amount)
            derived_terms += 1

        round_key = build_round_stitch_key(
            company_id=row["company_id"],
            round_date=round_date,
            round_type="reg_cf",
            instrument_type="equity",
            amount_raised=amount,
        )

        round_id = upsert_transaction_round(
            conn,
            company_id=row["company_id"],
            entity_id=row.get("entity_id"),
            country=row.get("country"),
            sector=row.get("sector"),
            stage_bucket=row.get("stage_bucket"),
            round_stitch_key=round_key,
            round_type="reg_cf",
            instrument_type="equity",
            round_date=round_date,
            amount_raised=amount,
            source_timestamp=datetime.combine(
                round_date or date.today(),
                datetime.min.time(),
                tzinfo=UTC,
            ),
            source_tier=tier,
        )
        rounds_upserted += 1

        insert_source_record(
            conn,
            transaction_round_id=round_id,
            source_name=f"crowdfunding_outcomes:{data_source or 'unknown'}",
            source_record_id=row["outcome_id"],
            source_url=None,
            source_timestamp=datetime.combine(
                round_date or date.today(),
                datetime.min.time(),
                tzinfo=UTC,
            ),
            source_tier=tier,
            raw_payload=_as_json_value(row),
        )

        facts_payload = {
            "round_type": "reg_cf",
            "instrument_type": "equity",
            "round_date": round_date,
            "amount_raised": amount,
            "pre_money_valuation": pre_money,
            "post_money_valuation": post_money,
            "arr_revenue": _safe_float(row.get("revenue_at_raise")),
            "overfunding_ratio": _safe_float(row.get("overfunding_ratio")),
        }
        for field_name, value in facts_payload.items():
            if value is None:
                continue
            insert_field_fact(
                conn,
                RoundFieldFact(
                    transaction_round_id=round_id,
                    field_name=field_name,
                    field_value=value,
                    source_name=f"crowdfunding_outcomes:{data_source or 'unknown'}",
                    source_record_id=row["outcome_id"],
                    source_tier=tier,
                    as_of_timestamp=datetime.combine(
                        round_date or date.today(),
                        datetime.min.time(),
                        tzinfo=UTC,
                    ),
                ),
            )
            facts_inserted += 1

    conn.commit()
    return {
        "rounds_upserted": rounds_upserted,
        "facts_inserted": facts_inserted,
        "derived_terms": derived_terms,
    }


def _extract_edgar_terms(text: str) -> dict[str, Any]:
    lower = text.lower()
    out: dict[str, Any] = {}

    cap_match = re.search(r"valuation\s+cap[^\d$]{0,40}\$?([\d,]{4,})", lower)
    if cap_match:
        out["valuation_cap"] = _safe_float(cap_match.group(1))

    discount_match = re.search(r"([0-9]{1,2}(?:\.[0-9]+)?)\s*%\s*discount", lower)
    if discount_match:
        out["discount_rate"] = _safe_float(discount_match.group(1) + "%")

    liq_match = re.search(r"([0-9](?:\.[0-9])?)x\s+liquidation\s+preference", lower)
    if liq_match:
        out["liquidation_preference_multiple"] = _safe_float(liq_match.group(1))

    if "pro rata" in lower or "pro-rata" in lower:
        out["pro_rata_rights"] = True

    if "participating preferred" in lower:
        out["liquidation_participation"] = "participating"
    elif "non-participating" in lower:
        out["liquidation_participation"] = "non_participating"

    return {k: v for k, v in out.items() if v is not None}


def _parse_scaled_amount(raw: str) -> float | None:
    match = re.match(r"^\s*([\d,]+(?:\.\d+)?)\s*(k|m|b|million|billion)?\s*$", raw, re.I)
    if not match:
        return _safe_float(raw)
    base = _safe_float(match.group(1))
    if base is None:
        return None
    unit = (match.group(2) or "").lower()
    if unit == "k":
        return base * 1_000
    if unit in {"m", "million"}:
        return base * 1_000_000
    if unit in {"b", "billion"}:
        return base * 1_000_000_000
    return base


def _extract_terms_from_form_c_text(text: str) -> dict[str, Any]:
    lower = text.lower()
    out: dict[str, Any] = {}

    pre_money_match = re.search(
        (
            r"pre[-\s]?money\s+valuation(?:\s*(?:of|is|:|=))?\s*\$?\s*"
            r"([\d,]+(?:\.\d+)?\s*(?:k|m|b|million|billion)?)"
        ),
        lower,
    )
    if pre_money_match:
        out["pre_money_valuation"] = _parse_scaled_amount(pre_money_match.group(1))

    post_money_match = re.search(
        (
            r"post[-\s]?money\s+valuation(?:\s*(?:of|is|:|=))?\s*\$?\s*"
            r"([\d,]+(?:\.\d+)?\s*(?:k|m|b|million|billion)?)"
        ),
        lower,
    )
    if post_money_match:
        out["post_money_valuation"] = _parse_scaled_amount(post_money_match.group(1))

    val_cap_match = re.search(
        r"valuation\s+cap(?:\s*(?:of|:|=))?\s*\$?\s*([\d,]+(?:\.\d+)?\s*(?:k|m|b|million|billion)?)",
        lower,
    )
    if val_cap_match:
        out["valuation_cap"] = _parse_scaled_amount(val_cap_match.group(1))

    discount_match = (
        re.search(r"discount(?:\s+rate)?(?:\s*(?:of|:|=))?\s*(\d{1,2}(?:\.\d+)?)\s*%", lower)
        or re.search(r"(\d{1,2}(?:\.\d+)?)\s*%\s*discount", lower)
    )
    if discount_match:
        out["discount_rate"] = _safe_float(discount_match.group(1) + "%")

    interest_match = (
        re.search(r"interest(?:\s+rate)?(?:\s*(?:of|:|=))?\s*(\d{1,2}(?:\.\d+)?)\s*%", lower)
        or re.search(r"(\d{1,2}(?:\.\d+)?)\s*%\s*interest", lower)
    )
    if interest_match:
        out["interest_rate"] = _safe_float(interest_match.group(1) + "%")

    maturity_match = re.search(
        (
            r"matur(?:ity|es?)(?:\s+date)?(?:\s*(?:on|:|=))?\s*"
            r"([A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4})"
        ),
        text,
        re.I,
    )
    if maturity_match:
        maturity = _safe_date(maturity_match.group(1))
        if maturity:
            out["maturity_date"] = maturity.isoformat()

    liq_pref_match = (
        re.search(r"(\d(?:\.\d+)?)x\s+liquidation\s+preference", lower)
        or re.search(r"liquidation\s+preference(?:\s*(?:of|:|=))?\s*(\d(?:\.\d+)?)x", lower)
    )
    if liq_pref_match:
        out["liquidation_preference_multiple"] = _safe_float(liq_pref_match.group(1))

    if re.search(r"non[-\s]?participating", lower):
        out["liquidation_participation"] = "non_participating"
    elif re.search(r"\bparticipating\b", lower):
        out["liquidation_participation"] = "participating"

    if re.search(r"no\s+pro[-\s]?rata", lower):
        out["pro_rata_rights"] = False
    elif re.search(r"pro[-\s]?rata(?:\s+rights?|\s+participation)?", lower):
        out["pro_rata_rights"] = True

    lead_match = re.search(
        r"(?:led\s+by|lead\s+investor[:\s])\s*([A-Z][A-Za-z0-9&.,' -]{2,60})",
        text,
    )
    if lead_match:
        out["lead_investor"] = lead_match.group(1).strip(" .,")

    return {k: v for k, v in out.items() if v is not None}


def ingest_terms_from_form_c_texts(
    conn: psycopg.Connection,
    *,
    max_rounds: int = 12000,
    min_text_length: int = 180,
) -> dict[str, int]:
    """Extract valuation/term hints from already-ingested Form C narrative text."""
    rows = execute_query(
        conn,
        """
        SELECT
          tr.id AS transaction_round_id,
          tr.round_date,
          tr.company_id,
          sft.id AS form_c_text_id,
          sft.filing_date,
          sft.narrative_text
        FROM transaction_rounds tr
        JOIN companies c ON c.id = tr.company_id
        JOIN LATERAL (
          SELECT s.id, s.filing_date, s.narrative_text
          FROM sec_form_c_texts s
          WHERE length(COALESCE(s.narrative_text, '')) >= %s
            AND (tr.round_date IS NULL OR s.filing_date <= tr.round_date)
            AND (
              s.company_id = tr.company_id
              OR (
                c.source_id IS NOT NULL
                AND regexp_replace(COALESCE(s.cik, ''), '^0+', '') =
                    regexp_replace(split_part(c.source_id, '_q', 1), '^0+', '')
              )
            )
          ORDER BY
            CASE WHEN s.company_id = tr.company_id THEN 0 ELSE 1 END,
            s.filing_date DESC NULLS LAST,
            s.created_at DESC NULLS LAST
          LIMIT 1
        ) sft ON true
        WHERE tr.country = 'US'
        ORDER BY tr.round_date DESC NULLS LAST
        LIMIT %s
        """,
        (min_text_length, max_rounds),
    )

    processed = 0
    facts_inserted = 0

    for row in rows:
        text = str(row.get("narrative_text") or "")
        extracted = _extract_terms_from_form_c_text(text)
        if not extracted:
            continue

        round_date = row.get("round_date")
        if isinstance(round_date, datetime):
            round_date = round_date.date()
        filing_date = row.get("filing_date")
        if isinstance(filing_date, datetime):
            filing_date = filing_date.date()

        insert_source_record(
            conn,
            transaction_round_id=row["transaction_round_id"],
            source_name="sec_form_c_texts_terms",
            source_record_id=str(row["form_c_text_id"]),
            source_url=None,
            source_timestamp=datetime.combine(
                filing_date or round_date or date.today(),
                datetime.min.time(),
                tzinfo=UTC,
            ),
            source_tier="B",
            raw_payload={"extracted_fields": sorted(extracted.keys())},
        )

        as_of = datetime.combine(
            filing_date or round_date or date.today(),
            datetime.min.time(),
            tzinfo=UTC,
        )
        for field_name, value in extracted.items():
            insert_field_fact(
                conn,
                RoundFieldFact(
                    transaction_round_id=row["transaction_round_id"],
                    field_name=field_name,
                    field_value=value,
                    source_name="sec_form_c_texts_terms",
                    source_record_id=str(row["form_c_text_id"]),
                    source_tier="B",
                    as_of_timestamp=as_of,
                ),
            )
            facts_inserted += 1
        processed += 1

    conn.commit()
    return {"rounds_processed": processed, "facts_inserted": facts_inserted}


def ingest_late_stage_terms_from_edgar(
    conn: psycopg.Connection,
    settings: Settings,
    *,
    max_rounds: int = 250,
) -> dict[str, int]:
    """Extract late-stage term hints from EDGAR filing corpus (8-K/S-1/424B).

    Writes extracted values as Tier-B facts; reconciliation decides whether they
    are accepted.
    """
    targets = execute_query(
        conn,
        """
        SELECT
          tr.id AS transaction_round_id,
          tr.company_id,
          tr.round_date,
          tr.round_stitch_key,
          tr.stage_bucket,
          tr.amount_raised,
          c.source_id,
          c.source
        FROM transaction_rounds tr
        JOIN companies c ON c.id = tr.company_id
        WHERE tr.country = 'US'
          AND (tr.stage_bucket = 'early_growth' OR tr.amount_raised >= 5000000)
          AND c.source IN ('sec_edgar', 'sec_form_d', 'sec_dera_cf')
        ORDER BY tr.round_date DESC NULLS LAST
        LIMIT %s
        """,
        (max_rounds,),
    )

    client = httpx.Client(
        headers={"User-Agent": settings.sec_user_agent, "Accept-Encoding": "gzip, deflate"},
        timeout=25.0,
        follow_redirects=True,
    )

    processed = 0
    facts_inserted = 0
    errors = 0

    try:
        for row in targets:
            cik_source = str(row.get("source_id") or "")
            cik = cik_source.split("_q", 1)[0].lstrip("0")
            if not cik:
                continue
            padded_cik = cik.zfill(10)

            try:
                resp = _rate_limited_get(client, SEC_SUBMISSIONS_URL.format(cik=padded_cik))
                payload = resp.json()
            except Exception:
                errors += 1
                continue

            recent = payload.get("filings", {}).get("recent", {})
            forms = recent.get("form", [])
            accession_numbers = recent.get("accessionNumber", [])
            primary_docs = recent.get("primaryDocument", [])
            filing_dates = recent.get("filingDate", [])
            if not forms:
                continue

            selected_idx = None
            for idx, form in enumerate(forms):
                if form in {"8-K", "S-1", "424B1", "424B2", "424B3", "424B4", "D"}:
                    selected_idx = idx
                    break
            if selected_idx is None:
                continue

            accession = str(accession_numbers[selected_idx]).replace("-", "")
            primary_doc = str(primary_docs[selected_idx])
            filing_date = _safe_date(filing_dates[selected_idx])

            if not accession or not primary_doc:
                continue

            filing_url = f"{SEC_ARCHIVES_BASE}/{int(cik)}/{accession}/{primary_doc}"
            try:
                filing_resp = _rate_limited_get(client, filing_url)
            except Exception:
                errors += 1
                continue
            text = filing_resp.text[:250_000]
            extracted = _extract_edgar_terms(text)
            if not extracted:
                processed += 1
                continue

            insert_source_record(
                conn,
                transaction_round_id=row["transaction_round_id"],
                source_name="sec_edgar_primary_docs",
                source_record_id=f"{padded_cik}:{accession}",
                source_url=filing_url,
                source_timestamp=datetime.combine(
                    filing_date or date.today(),
                    datetime.min.time(),
                    tzinfo=UTC,
                ),
                source_tier="B",
                raw_payload={
                    "form": forms[selected_idx],
                    "accession": accession,
                    "primary_doc": primary_doc,
                },
            )

            as_of = datetime.combine(
                filing_date or date.today(),
                datetime.min.time(),
                tzinfo=UTC,
            )
            for field_name, value in extracted.items():
                insert_field_fact(
                    conn,
                    RoundFieldFact(
                        transaction_round_id=row["transaction_round_id"],
                        field_name=field_name,
                        field_value=value,
                        source_name="sec_edgar_primary_docs",
                        source_record_id=f"{padded_cik}:{accession}",
                        source_tier="B",
                        as_of_timestamp=as_of,
                    ),
                )
                facts_inserted += 1

            processed += 1

        conn.commit()
    finally:
        client.close()

    return {
        "rounds_processed": processed,
        "facts_inserted": facts_inserted,
        "errors": errors,
    }


def ingest_uk_private_round_spine(
    conn: psycopg.Connection,
    settings: Settings,
    *,
    company_numbers: list[str],
    max_filings_per_company: int = 80,
) -> dict[str, int]:
    """Build UK private-round spine from Companies House SH01 + status signals."""
    if not settings.companies_house_api_key:
        logger.warning("companies_house_key_missing")
        return {"companies": 0, "rounds_upserted": 0, "facts_inserted": 0, "errors": 0}

    client = httpx.Client(
        base_url=COMPANIES_HOUSE_BASE,
        auth=(settings.companies_house_api_key, ""),
        timeout=15.0,
        headers={"Accept": "application/json"},
    )

    companies = 0
    rounds = 0
    facts = 0
    errors = 0

    try:
        for company_number in company_numbers:
            companies += 1
            try:
                time.sleep(0.55)
                comp_resp = client.get(f"/company/{company_number}")
                if comp_resp.status_code == 404:
                    continue
                comp_resp.raise_for_status()
                profile = comp_resp.json()

                time.sleep(0.55)
                filings_resp = client.get(
                    f"/company/{company_number}/filing-history",
                    params={"items_per_page": max_filings_per_company},
                )
                filings_resp.raise_for_status()
                filings = filings_resp.json().get("items", [])

                company_row = execute_query(
                    conn,
                    """
                    SELECT id, entity_id, country, sector
                    FROM companies
                    WHERE source = 'companies_house' AND source_id = %s
                    LIMIT 1
                    """,
                    (company_number,),
                )
                if not company_row:
                    continue
                company_id = company_row[0]["id"]
                entity_id = company_row[0].get("entity_id")
                country = company_row[0].get("country") or "UK"
                sector = company_row[0].get("sector")

                # Status + risk signals as official traction rows.
                status = profile.get("company_status")
                if status:
                    execute_query(
                        conn,
                        """
                        INSERT INTO official_traction_signals (
                          company_id, entity_id, signal_type, signal_date, signal_value,
                          confidence, source_name, source_tier, details
                        )
                        VALUES (
                          %s, %s, 'uk_public_contracts', CURRENT_DATE, NULL, 0.2,
                          'companies_house_status', 'B', %s::jsonb
                        )
                        """,
                        (
                            company_id,
                            entity_id,
                            _safe_json_dumps({"company_status": status}),
                        ),
                    )

                for filing in filings:
                    filing_type = str(filing.get("type") or "").upper()
                    if filing_type != "SH01":
                        continue

                    filed_on = _safe_date(filing.get("date") or filing.get("action_date"))
                    desc_values = filing.get("description_values") or {}
                    nominal = _safe_float(
                        desc_values.get("nominal_value_per_share")
                        or desc_values.get("aggregate_nominal_value")
                        or desc_values.get("amount")
                    )
                    shares = _safe_float(
                        desc_values.get("number_allotted")
                        or desc_values.get("total_number_of_shares_allotted")
                    )
                    amount = nominal
                    if nominal is not None and shares is not None:
                        amount = nominal * shares

                    round_key = build_round_stitch_key(
                        company_id=company_id,
                        round_date=filed_on,
                        round_type="uk_share_allotment",
                        instrument_type="equity",
                        amount_raised=amount,
                    )

                    round_id = upsert_transaction_round(
                        conn,
                        company_id=company_id,
                        entity_id=entity_id,
                        country=country,
                        sector=sector,
                        stage_bucket=None,
                        round_stitch_key=round_key,
                        round_type="uk_share_allotment",
                        instrument_type="equity",
                        round_date=filed_on,
                        amount_raised=amount,
                        source_timestamp=datetime.combine(
                            filed_on or date.today(),
                            datetime.min.time(),
                            tzinfo=UTC,
                        ),
                        source_tier="A",
                    )
                    rounds += 1

                    transaction_id = filing.get("transaction_id") or filing.get("barcode")
                    insert_source_record(
                        conn,
                        transaction_round_id=round_id,
                        source_name="companies_house_sh01",
                        source_record_id=str(transaction_id) if transaction_id else None,
                        source_url=filing.get("links", {}).get("self"),
                        source_timestamp=datetime.combine(
                            filed_on or date.today(),
                            datetime.min.time(),
                            tzinfo=UTC,
                        ),
                        source_tier="A",
                        raw_payload=_as_json_value(filing),
                    )

                    facts_to_write = {
                        "round_type": "uk_share_allotment",
                        "instrument_type": "equity",
                        "round_date": filed_on,
                        "amount_raised": amount,
                        "post_money_valuation": None,
                        "pre_money_valuation": None,
                    }
                    for field_name, value in facts_to_write.items():
                        if value is None:
                            continue
                        insert_field_fact(
                            conn,
                            RoundFieldFact(
                                transaction_round_id=round_id,
                                field_name=field_name,
                                field_value=value,
                                source_name="companies_house_sh01",
                                source_record_id=str(transaction_id) if transaction_id else None,
                                source_tier="A",
                                as_of_timestamp=datetime.combine(
                                    filed_on or date.today(),
                                    datetime.min.time(),
                                    tzinfo=UTC,
                                ),
                            ),
                        )
                        facts += 1

            except Exception:
                errors += 1

        conn.commit()
    finally:
        client.close()

    return {
        "companies": companies,
        "rounds_upserted": rounds,
        "facts_inserted": facts,
        "errors": errors,
    }


def _coerce_fact_value(field_name: str, value: Any) -> Any:
    if field_name in _NUMERIC_FIELDS:
        return _safe_float(value)
    if field_name in _BOOLEAN_FIELDS:
        return _safe_bool(value)
    if field_name in _DATE_FIELDS:
        parsed = _safe_date(value)
        return parsed.isoformat() if parsed else None
    if value is None:
        return None
    return str(value).strip()


def _is_conflict(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return False
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        denom = max(abs(float(a)), abs(float(b)), 1.0)
        return abs(float(a) - float(b)) / denom > 0.05
    return str(a).lower() != str(b).lower()


def _completeness_fields_for_round(truth_map: dict[str, Any]) -> tuple[str, ...]:
    instrument = str(truth_map.get("instrument_type") or "").lower()
    round_type = str(truth_map.get("round_type") or "").lower()
    baseline = ("round_type", "instrument_type", "amount_raised")
    is_convertible = any(
        token in instrument or token in round_type
        for token in ("safe", "note", "convertible", "debt")
    )
    if is_convertible:
        return baseline + ("valuation_cap", "discount_rate")
    return baseline + ("pre_money_valuation", "post_money_valuation")


def reconcile_transaction_round_fields(
    conn: psycopg.Connection,
    *,
    limit_rounds: int | None = None,
    batch_commit_size: int = 1000,
) -> dict[str, int]:
    """Reconcile field-level facts into transaction truth rows + round confidence."""
    query = """
        SELECT DISTINCT transaction_round_id
        FROM transaction_round_field_facts
        ORDER BY transaction_round_id
    """
    params: tuple[Any, ...] = ()
    if limit_rounds is not None:
        query += " LIMIT %s"
        params = (limit_rounds,)

    rounds = execute_query(conn, query, params)

    rounds_processed = 0
    truths_upserted = 0

    for round_row in rounds:
        round_id = round_row["transaction_round_id"]
        facts = execute_query(
            conn,
            """
            SELECT
              field_name, field_value, source_name, source_record_id,
              source_tier, as_of_timestamp
            FROM transaction_round_field_facts
            WHERE transaction_round_id = %s
            ORDER BY field_name, as_of_timestamp DESC NULLS LAST, created_at DESC
            """,
            (round_id,),
        )
        by_field: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for fact in facts:
            by_field[fact["field_name"]].append(fact)

        valuation_conflicts = 0
        confidence_values: list[float] = []

        for field_name, field_facts in by_field.items():
            canonical_values: list[tuple[Any, dict[str, Any]]] = []
            for fact in field_facts:
                raw = fact.get("field_value")
                value = None
                if isinstance(raw, dict):
                    value = raw.get("value")
                elif isinstance(raw, str):
                    try:
                        parsed = json.loads(raw)
                        if isinstance(parsed, dict):
                            value = parsed.get("value")
                    except json.JSONDecodeError:
                        value = raw
                coerced = _coerce_fact_value(field_name, value)
                canonical_values.append((coerced, fact))

            selected_value = None
            selected_fact = None
            for value, fact in sorted(
                canonical_values,
                key=lambda item: (
                    _tier_rank(str(item[1].get("source_tier") or "C")),
                    item[1].get("as_of_timestamp") or datetime(1970, 1, 1, tzinfo=UTC),
                ),
                reverse=True,
            ):
                if value is not None:
                    selected_value = value
                    selected_fact = fact
                    break
            if selected_fact is None:
                continue

            conflict_state = "none"
            for value, _fact in canonical_values:
                if _is_conflict(selected_value, value):
                    conflict_state = (
                        "major"
                        if _tier_rank(str(_fact.get("source_tier") or "C")) >= 2
                        else "minor"
                    )
                    break

            source_names = sorted({str(f[1].get("source_name") or "") for f in canonical_values})
            source_record_ids = sorted(
                {
                    str(f[1].get("source_record_id") or "")
                    for f in canonical_values
                    if f[1].get("source_record_id")
                }
            )

            tier = str(selected_fact.get("source_tier") or "C")
            base_conf = {"A": 0.92, "B": 0.78, "C": 0.58}.get(tier, 0.5)
            evidence_bonus = min(0.08, max(0, len(canonical_values) - 1) * 0.02)
            conflict_penalty = (
                0.35 if conflict_state == "major" else (0.15 if conflict_state == "minor" else 0.0)
            )
            confidence = max(0.0, min(1.0, base_conf + evidence_bonus - conflict_penalty))
            confidence_values.append(confidence)

            if field_name in _VALUATION_CRITICAL_FIELDS and conflict_state == "major":
                valuation_conflicts += 1

            execute_query(
                conn,
                """
                INSERT INTO transaction_round_field_truth (
                  transaction_round_id,
                  field_name,
                  reconciled_value,
                  source_names,
                  source_record_ids,
                  as_of_timestamp,
                  conflict_state,
                  confidence,
                  evidence_count,
                  updated_at
                )
                VALUES (%s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s, %s, now())
                ON CONFLICT (transaction_round_id, field_name) DO UPDATE
                SET
                  reconciled_value = EXCLUDED.reconciled_value,
                  source_names = EXCLUDED.source_names,
                  source_record_ids = EXCLUDED.source_record_ids,
                  as_of_timestamp = EXCLUDED.as_of_timestamp,
                  conflict_state = EXCLUDED.conflict_state,
                  confidence = EXCLUDED.confidence,
                  evidence_count = EXCLUDED.evidence_count,
                  updated_at = now()
                """,
                (
                    round_id,
                    field_name,
                    _safe_json_dumps({"value": selected_value}),
                    _safe_json_dumps(source_names),
                    _safe_json_dumps(source_record_ids),
                    selected_fact.get("as_of_timestamp"),
                    conflict_state,
                    confidence,
                    len(canonical_values),
                ),
            )
            truths_upserted += 1

        truth_rows = execute_query(
            conn,
            """
            SELECT field_name, reconciled_value, conflict_state, confidence, as_of_timestamp,
                   source_names, source_record_ids
            FROM transaction_round_field_truth
            WHERE transaction_round_id = %s
            """,
            (round_id,),
        )

        truth_map: dict[str, Any] = {}
        source_names_all: set[str] = set()
        max_as_of: datetime | None = None
        for row in truth_rows:
            raw = row.get("reconciled_value")
            val = None
            if isinstance(raw, dict):
                val = raw.get("value")
            elif isinstance(raw, str):
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        val = parsed.get("value")
                except json.JSONDecodeError:
                    val = raw
            truth_map[row["field_name"]] = _coerce_fact_value(row["field_name"], val)

            names = row.get("source_names")
            if isinstance(names, list):
                source_names_all.update(str(n) for n in names)
            elif isinstance(names, str):
                try:
                    parsed_names = json.loads(names)
                    if isinstance(parsed_names, list):
                        source_names_all.update(str(n) for n in parsed_names)
                except json.JSONDecodeError:
                    pass
            as_of = row.get("as_of_timestamp")
            if isinstance(as_of, datetime):
                max_as_of = max_as_of or as_of
                if as_of > max_as_of:
                    max_as_of = as_of

        completeness_fields = _completeness_fields_for_round(truth_map)
        core_populated = sum(
            1
            for field_name in completeness_fields
            if truth_map.get(field_name) not in (None, "", "unknown")
        )
        core_term_completeness = (
            core_populated / len(completeness_fields) if completeness_fields else 0.0
        )
        avg_conf = sum(confidence_values) / len(confidence_values) if confidence_values else 0.0

        if avg_conf >= 0.8 and valuation_conflicts == 0 and core_term_completeness >= 0.85:
            confidence_band = "high"
        elif avg_conf >= 0.65 and valuation_conflicts <= 1 and core_term_completeness >= 0.6:
            confidence_band = "medium"
        else:
            confidence_band = "low"

        source_tier = "C"
        if any(_source_tier_for_name(name) == "A" for name in source_names_all):
            source_tier = "A"
        elif any(_source_tier_for_name(name) == "B" for name in source_names_all):
            source_tier = "B"

        execute_query(
            conn,
            """
            UPDATE transaction_rounds
            SET
              round_type = COALESCE(%s, round_type),
              instrument_type = COALESCE(%s, instrument_type),
              round_date = COALESCE(%s::date, round_date),
              amount_raised = COALESCE(%s, amount_raised),
              pre_money_valuation = COALESCE(%s, pre_money_valuation),
              post_money_valuation = COALESCE(%s, post_money_valuation),
              valuation_cap = COALESCE(%s, valuation_cap),
              discount_rate = COALESCE(%s, discount_rate),
              interest_rate = COALESCE(%s, interest_rate),
              maturity_date = COALESCE(%s::date, maturity_date),
              liquidation_preference_multiple = COALESCE(%s, liquidation_preference_multiple),
              liquidation_participation = COALESCE(%s, liquidation_participation),
              pro_rata_rights = COALESCE(%s, pro_rata_rights),
              lead_investor = COALESCE(%s, lead_investor),
              arr_revenue = COALESCE(%s, arr_revenue),
              revenue_growth_yoy = COALESCE(%s, revenue_growth_yoy),
              burn_rate_monthly = COALESCE(%s, burn_rate_monthly),
              runway_months = COALESCE(%s, runway_months),
              source_timestamp = COALESCE(%s, source_timestamp),
              source_tier = %s,
              source_count = %s,
              conflict_count = %s,
              core_term_completeness = %s,
              confidence_score = %s,
              confidence_band = %s,
              updated_at = now()
            WHERE id = %s
            """,
            (
                truth_map.get("round_type"),
                truth_map.get("instrument_type"),
                truth_map.get("round_date"),
                truth_map.get("amount_raised"),
                truth_map.get("pre_money_valuation"),
                truth_map.get("post_money_valuation"),
                truth_map.get("valuation_cap"),
                truth_map.get("discount_rate"),
                truth_map.get("interest_rate"),
                truth_map.get("maturity_date"),
                truth_map.get("liquidation_preference_multiple"),
                truth_map.get("liquidation_participation"),
                truth_map.get("pro_rata_rights"),
                truth_map.get("lead_investor"),
                truth_map.get("arr_revenue"),
                truth_map.get("revenue_growth_yoy"),
                truth_map.get("burn_rate_monthly"),
                truth_map.get("runway_months"),
                max_as_of,
                source_tier,
                len(source_names_all),
                valuation_conflicts,
                core_term_completeness,
                avg_conf,
                confidence_band,
                round_id,
            ),
        )

        rounds_processed += 1
        if batch_commit_size > 0 and rounds_processed % batch_commit_size == 0:
            conn.commit()
            logger.info(
                "transaction_truth_reconcile_progress",
                rounds_processed=rounds_processed,
                truth_fields_upserted=truths_upserted,
            )

    conn.commit()
    return {
        "rounds_processed": rounds_processed,
        "truth_fields_upserted": truths_upserted,
    }


def apply_valuation_truth_gate(
    conn: psycopg.Connection,
    *,
    min_core_term_completeness: float = 0.85,
    min_core_term_completeness_tier_a: float = 0.60,
    min_core_term_completeness_tier_b: float = 0.70,
    max_conflicts: int = 0,
    batch_commit_size: int = 2000,
) -> dict[str, int]:
    """Apply strict valuation confidence gates at transaction-round level."""
    rounds = execute_query(
        conn,
        """
        SELECT id, core_term_completeness, conflict_count, source_tier, confidence_band
        FROM transaction_rounds
        """,
    )
    passed = 0
    blocked = 0
    strict_shortfall = 0

    for row in rounds:
        completeness = float(row.get("core_term_completeness") or 0)
        conflicts = int(row.get("conflict_count") or 0)
        source_tier = str(row.get("source_tier") or "C")
        confidence_band = str(row.get("confidence_band") or "low")

        reasons: list[str] = []
        required_completeness = min_core_term_completeness
        if source_tier == "A":
            required_completeness = min_core_term_completeness_tier_a
        elif source_tier == "B":
            required_completeness = min_core_term_completeness_tier_b

        if completeness < required_completeness:
            reasons.append(f"core_term_completeness<{required_completeness:.2f}")
        if completeness < min_core_term_completeness:
            strict_shortfall += 1
        if conflicts > max_conflicts:
            reasons.append(f"valuation_conflicts>{max_conflicts}")
        if source_tier == "C":
            reasons.append("source_tier_c")
        if confidence_band == "low":
            reasons.append("low_confidence")

        gate_pass = len(reasons) == 0
        if gate_pass:
            passed += 1
        else:
            blocked += 1

        execute_query(
            conn,
            """
            UPDATE transaction_rounds
            SET valuation_gate_pass = %s,
                valuation_gate_reason = %s,
                updated_at = now()
            WHERE id = %s
            """,
            (
                gate_pass,
                None
                if gate_pass
                else "Deep diligence only; valuation confidence insufficient ("
                + ",".join(reasons)
                + ")",
                row["id"],
            ),
        )
        total_processed = passed + blocked
        if batch_commit_size > 0 and total_processed % batch_commit_size == 0:
            conn.commit()
            logger.info(
                "transaction_truth_gate_progress",
                rounds_processed=total_processed,
                rounds_passed=passed,
                rounds_blocked=blocked,
            )

    conn.commit()
    return {
        "rounds_passed": passed,
        "rounds_blocked": blocked,
        "strict_shortfall": strict_shortfall,
    }


def _extract_adv_latest_zip_links(html: str) -> list[str]:
    links = re.findall(r'href="([^"]+)"', html, flags=re.IGNORECASE)
    candidates: list[tuple[datetime, str]] = []
    for link in links:
        low = link.lower()
        if not low.endswith(".zip"):
            continue
        if "investment" not in low and "adviser" not in low and "ia" not in low:
            continue
        if link.startswith("/"):
            link = "https://www.sec.gov" + link
        filename = link.rsplit("/", 1)[-1].lower()
        match = re.search(r"ia(\d{6})", filename)
        if not match:
            continue
        try:
            stamp = datetime.strptime(match.group(1), "%m%d%y")
        except ValueError:
            continue
        candidates.append((stamp, link))
    seen: set[str] = set()
    ordered: list[str] = []
    for _stamp, link in sorted(
        candidates,
        key=lambda item: (item[0], "exempt" not in item[1].lower()),
        reverse=True,
    ):
        if link in seen:
            continue
        seen.add(link)
        ordered.append(link)
    return ordered


def _parse_adv_rows_from_zip(raw_zip: bytes) -> list[dict[str, Any]]:
    with zipfile.ZipFile(io.BytesIO(raw_zip)) as zf:
        names = [
            n
            for n in zf.namelist()
            if n.lower().endswith((".csv", ".txt", ".xlsx", ".xlsm"))
        ]
        if not names:
            return []
        target = sorted(names)[0]
        data = zf.read(target)

    if target.lower().endswith((".xlsx", ".xlsm")):
        df = pd.read_excel(io.BytesIO(data), sheet_name=0, dtype=object)
        rows: list[dict[str, Any]] = []
        for record in df.to_dict(orient="records"):
            normalized: dict[str, Any] = {}
            for key, value in record.items():
                if key is None:
                    continue
                normalized_key = str(key).strip().lower()
                if not normalized_key:
                    continue
                if pd.isna(value):
                    normalized[normalized_key] = None
                elif isinstance(value, str):
                    normalized[normalized_key] = value.strip()
                else:
                    normalized[normalized_key] = value
            if normalized:
                rows.append(normalized)
        return rows

    text = data.decode("utf-8", errors="replace")
    sample = text[:2048]
    delimiter = "\t" if sample.count("\t") > sample.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    rows: list[dict[str, Any]] = []
    for row in reader:
        normalized = {
            str(k).strip().lower(): (v.strip() if isinstance(v, str) else v)
            for k, v in row.items()
            if k
        }
        rows.append(normalized)
    return rows


def _pick_adv_field(row: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        if key in row and row[key]:
            return str(row[key]).strip()
    return None


def _parse_adv_numeric(value: Any) -> float | None:
    direct = _safe_float(value)
    if direct is not None:
        return direct
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if "billion" in text:
        matched = re.search(r"([\d,]+(?:\.\d+)?)", text)
        if matched:
            base = _safe_float(matched.group(1))
            if base is not None:
                return base * 1_000_000_000
        return 1_000_000_000
    if "million" in text:
        matched = re.search(r"([\d,]+(?:\.\d+)?)", text)
        if matched:
            base = _safe_float(matched.group(1))
            if base is not None:
                return base * 1_000_000
    matched = re.search(r"([\d,]+(?:\.\d+)?)", text)
    if matched:
        return _safe_float(matched.group(1))
    return None


def ingest_form_adv_investor_reference(
    conn: psycopg.Connection,
    settings: Settings,
    *,
    max_rows: int = 150_000,
) -> dict[str, int]:
    """Load free SEC Form ADV adviser disclosure dataset and classify investor quality."""
    client = httpx.Client(headers={"User-Agent": settings.sec_user_agent}, timeout=30.0)
    inserted = 0
    linked = 0
    scanned = 0

    try:
        page = _rate_limited_get(client, SEC_FORM_ADV_PAGE).text
        links = _extract_adv_latest_zip_links(page)
        if not links:
            logger.warning("form_adv_links_not_found")
            return {"inserted": 0, "linked_rounds": 0, "scanned": 0}

        raw_zip = None
        selected_link = None
        for link in links[:6]:
            try:
                raw_zip = _rate_limited_get(client, link).content
                if raw_zip:
                    selected_link = link
                    break
            except Exception:
                continue

        if not raw_zip:
            logger.warning("form_adv_download_failed")
            return {"inserted": 0, "linked_rounds": 0, "scanned": 0}

        rows = _parse_adv_rows_from_zip(raw_zip)
        for row in rows[:max_rows]:
            legal_name = _pick_adv_field(
                row,
                (
                    "legal_name",
                    "legal name",
                    "firm_name",
                    "name",
                    "primary_business_name",
                    "primary business name",
                ),
            )
            if not legal_name:
                continue
            scanned += 1

            sec_id = _pick_adv_field(
                row,
                (
                    "sec_number",
                    "sec number",
                    "sec#",
                    "sec_file_no",
                    "sec_file_number",
                ),
            )
            crd = _pick_adv_field(
                row,
                ("crd_number", "crd", "firm_crd_number", "organization crd#", "organization crd"),
            )
            aum = _parse_adv_numeric(
                _pick_adv_field(
                    row,
                    (
                        "regulatory_assets_under_management",
                        "assets_under_management",
                        "aum",
                        "1o - if yes, approx. amount of assets",
                        "total gross assets of private funds",
                    ),
                )
            )
            exempt = _pick_adv_field(
                row,
                ("exempt_reporting_adviser", "is_exempt", "is exempt reporting adviser"),
            )
            disclosure_counts = [
                _parse_adv_numeric(v)
                for k, v in row.items()
                if "disclosure" in str(k).lower() and v not in (None, "")
            ]
            disclosure_sum = sum(x for x in disclosure_counts if x is not None)
            disciplinary = _parse_adv_numeric(
                _pick_adv_field(row, ("number_of_disclosures", "disciplinary_events"))
            )
            if disciplinary is None and disclosure_sum > 0:
                disciplinary = disclosure_sum

            adviser_status = "registered"
            if selected_link and "exempt" in selected_link.lower():
                adviser_status = "exempt"
            if exempt and str(exempt).lower() in {"y", "yes", "true", "1", "exempt"}:
                adviser_status = "exempt"
            sec_status = _pick_adv_field(row, ("sec current status", "sec_status"))
            if sec_status and "exempt" in sec_status.lower():
                adviser_status = "exempt"

            quality_tier = "C"
            if adviser_status == "registered" and aum is not None and aum >= 1_000_000_000:
                quality_tier = "A"
            elif adviser_status == "registered":
                quality_tier = "B"

            execute_query(
                conn,
                """
                INSERT INTO investor_references (
                  sec_identifier,
                  crd_number,
                  legal_name,
                  normalized_name,
                  adviser_status,
                  regulatory_assets_usd,
                  disciplinary_events,
                  quality_tier,
                  source_name,
                  source_timestamp,
                  raw_payload,
                  updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'sec_form_adv', now(), %s::jsonb, now())
                ON CONFLICT (normalized_name) DO UPDATE
                SET
                  sec_identifier = COALESCE(
                    EXCLUDED.sec_identifier,
                    investor_references.sec_identifier
                  ),
                  crd_number = COALESCE(
                    EXCLUDED.crd_number,
                    investor_references.crd_number
                  ),
                  adviser_status = EXCLUDED.adviser_status,
                  regulatory_assets_usd = COALESCE(
                    EXCLUDED.regulatory_assets_usd,
                    investor_references.regulatory_assets_usd
                  ),
                  disciplinary_events = COALESCE(
                    EXCLUDED.disciplinary_events,
                    investor_references.disciplinary_events
                  ),
                  quality_tier = EXCLUDED.quality_tier,
                  source_timestamp = now(),
                  raw_payload = EXCLUDED.raw_payload,
                  updated_at = now()
                """,
                (
                    sec_id,
                    crd,
                    legal_name,
                    _normalize_name(legal_name),
                    adviser_status,
                    aum,
                    int(disciplinary) if disciplinary is not None else 0,
                    quality_tier,
                    _safe_json_dumps(_as_json_value(row)),
                ),
            )
            inserted += 1

        # Link lead investors on transaction rounds to reference quality.
        rounds = execute_query(
            conn,
            """
            SELECT id, lead_investor
            FROM transaction_rounds
            WHERE lead_investor IS NOT NULL AND lead_investor <> ''
            """,
        )
        for row in rounds:
            norm = _normalize_name(row["lead_investor"])
            if not norm:
                continue
            ref = execute_query(
                conn,
                """
                SELECT id, quality_tier, regulatory_assets_usd
                FROM investor_references
                WHERE normalized_name = %s
                LIMIT 1
                """,
                (norm,),
            )
            if not ref:
                ref = execute_query(
                    conn,
                    """
                    SELECT id, quality_tier, regulatory_assets_usd
                    FROM investor_references
                    WHERE normalized_name LIKE %s
                    ORDER BY
                      CASE quality_tier WHEN 'A' THEN 0 WHEN 'B' THEN 1 ELSE 2 END,
                      regulatory_assets_usd DESC NULLS LAST
                    LIMIT 1
                    """,
                    (f"%{norm}%",),
                )
            if not ref:
                continue

            tier = ref[0]["quality_tier"]
            quality_score = 1.0 if tier == "A" else (0.75 if tier == "B" else 0.45)
            execute_query(
                conn,
                """
                UPDATE transaction_rounds
                SET lead_investor_quality = %s,
                    updated_at = now()
                WHERE id = %s
                """,
                (quality_score, row["id"]),
            )
            execute_query(
                conn,
                """
                INSERT INTO transaction_round_investors (
                  transaction_round_id,
                  investor_name,
                  normalized_name,
                  investor_role,
                  investor_reference_id,
                  quality_score,
                  source_name,
                  source_record_id,
                  as_of_timestamp
                )
                VALUES (%s, %s, %s, 'lead', %s, %s, 'sec_form_adv', %s, now())
                ON CONFLICT (transaction_round_id, normalized_name, investor_role) DO UPDATE
                SET
                  investor_reference_id = EXCLUDED.investor_reference_id,
                  quality_score = EXCLUDED.quality_score,
                  source_name = EXCLUDED.source_name,
                  source_record_id = EXCLUDED.source_record_id,
                  as_of_timestamp = now()
                """,
                (
                    row["id"],
                    row["lead_investor"],
                    norm,
                    ref[0]["id"],
                    quality_score,
                    str(ref[0]["id"]),
                ),
            )
            linked += 1

        conn.commit()
    finally:
        client.close()

    return {"inserted": inserted, "linked_rounds": linked, "scanned": scanned}


def ingest_official_traction_signals(
    conn: psycopg.Connection,
    settings: Settings,
    *,
    limit_companies: int = 200,
) -> dict[str, int]:
    """Enrich traction with free official UK contracts, UKRI grants, and patent signals."""
    targets = execute_query(
        conn,
        """
        SELECT id AS company_id, entity_id, name, country
        FROM companies
        WHERE source IN ('companies_house', 'sec_edgar', 'sec_dera_cf', 'sec_form_d')
        ORDER BY created_at DESC
        LIMIT %s
        """,
        (limit_companies,),
    )

    client = httpx.Client(timeout=25.0, headers={"User-Agent": settings.sec_user_agent})
    inserted = 0
    errors = 0

    try:
        for row in targets:
            company_name = str(row.get("name") or "").strip()
            if not company_name:
                continue
            company_id = row["company_id"]
            entity_id = row.get("entity_id")

            # UK public contracts (open search page endpoint; free, no key).
            if str(row.get("country") or "").upper() in {"UK", "GB", "UNITED KINGDOM"}:
                try:
                    resp = _rate_limited_get(
                        client,
                        CONTRACTS_FINDER_SEARCH,
                        params={"q": company_name, "size": 1},
                    )
                    details = {
                        "query": company_name,
                        "status_code": resp.status_code,
                        "url": str(resp.url),
                    }
                    execute_query(
                        conn,
                        """
                        INSERT INTO official_traction_signals (
                          company_id, entity_id, signal_type, signal_date,
                          signal_value, confidence, source_name, source_tier,
                          source_url, details
                        ) VALUES (
                          %s, %s, 'uk_public_contracts', CURRENT_DATE, %s, %s,
                          %s, %s, %s, %s::jsonb
                        )
                        """,
                        (
                            company_id,
                            entity_id,
                            1,
                            0.45,
                            "contracts_finder_search",
                            "B",
                            str(resp.url),
                            _safe_json_dumps(details),
                        ),
                    )
                    inserted += 1
                except Exception:
                    errors += 1

                # UKRI grant search (GTR API).
                try:
                    resp = _rate_limited_get(
                        client,
                        f"{UKRI_GTR_BASE}/projects",
                        params={"s": company_name, "p": 1, "size": 1},
                    )
                    execute_query(
                        conn,
                        """
                        INSERT INTO official_traction_signals (
                          company_id, entity_id, signal_type, signal_date,
                          signal_value, confidence, source_name, source_tier,
                          source_url, details
                        ) VALUES (
                          %s, %s, 'ukri_grants', CURRENT_DATE, %s, %s,
                          %s, %s, %s, %s::jsonb
                        )
                        """,
                        (
                            company_id,
                            entity_id,
                            1,
                            0.55,
                            "ukri_gtr_search",
                            "B",
                            str(resp.url),
                            _safe_json_dumps(
                                {"query": company_name, "status_code": resp.status_code}
                            ),
                        ),
                    )
                    inserted += 1
                except Exception:
                    errors += 1

            # PatentsView query by assignee name.
            try:
                payload = {
                    "q": {
                        "_contains": {
                            "assignee_organization": company_name,
                        },
                    },
                    "f": ["patent_number"],
                    "o": {"per_page": 1},
                }
                time.sleep(_MIN_REQUEST_INTERVAL)
                resp = client.post(PATENTSVIEW_SEARCH, json=payload)
                if resp.status_code < 500:
                    data = resp.json()
                    count = int(data.get("total_patent_count") or 0)
                    execute_query(
                        conn,
                        """
                        INSERT INTO official_traction_signals (
                          company_id, entity_id, signal_type, signal_date,
                          signal_value, confidence, source_name, source_tier,
                          source_url, details
                        ) VALUES (
                          %s, %s, 'uspto_patents', CURRENT_DATE, %s, %s,
                          %s, %s, %s, %s::jsonb
                        )
                        """,
                        (
                            company_id,
                            entity_id,
                            count,
                            0.5,
                            "patentsview_api",
                            "B",
                            PATENTSVIEW_SEARCH,
                            _safe_json_dumps({"query": company_name, "total_patent_count": count}),
                        ),
                    )
                    inserted += 1
            except Exception:
                errors += 1

        conn.commit()
    finally:
        client.close()

    return {"signals_inserted": inserted, "errors": errors, "companies_scanned": len(targets)}
