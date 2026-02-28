"""Companies House API pipeline.

Fetches company profiles, filing history, and officer data for UK companies.
Rate limit: 600 requests per 5 minutes (~2 req/s).
API key required (free registration at developer.company-information.service.gov.uk).
"""

from __future__ import annotations

import time
from datetime import date
from typing import Any

import httpx
import structlog

from startuplens.config import Settings
from startuplens.db import execute_many, execute_query

logger = structlog.get_logger(__name__)

BASE_URL = "https://api.company-information.service.gov.uk"
RATE_LIMIT_PER_5MIN = 600
REQUEST_INTERVAL = 5 * 60 / RATE_LIMIT_PER_5MIN  # ~0.5s between requests


def _make_client(api_key: str) -> httpx.Client:
    """Create an httpx client with Companies House basic auth."""
    return httpx.Client(
        base_url=BASE_URL,
        auth=(api_key, ""),
        timeout=10.0,
        headers={"Accept": "application/json"},
    )


def fetch_company_profile(client: httpx.Client, company_number: str) -> dict | None:
    """Fetch a single company profile by number.

    Returns None if the company is not found (404).
    """
    try:
        resp = client.get(f"/company/{company_number}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError:
        logger.warning("companies_house_api_error", company_number=company_number)
        return None


def fetch_filing_history(
    client: httpx.Client,
    company_number: str,
    items_per_page: int = 25,
) -> list[dict]:
    """Fetch the most recent filings for a company."""
    try:
        resp = client.get(
            f"/company/{company_number}/filing-history",
            params={"items_per_page": items_per_page},
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("items", [])
    except httpx.HTTPStatusError:
        logger.warning("filing_history_error", company_number=company_number)
        return []


def fetch_officers(client: httpx.Client, company_number: str) -> list[dict]:
    """Fetch current officers for a company."""
    try:
        resp = client.get(f"/company/{company_number}/officers")
        resp.raise_for_status()
        data = resp.json()
        return data.get("items", [])
    except httpx.HTTPStatusError:
        logger.warning("officers_error", company_number=company_number)
        return []


def normalize_company_profile(raw: dict) -> dict:
    """Normalize a Companies House profile into our schema fields."""
    incorporation_date = raw.get("date_of_creation")
    if incorporation_date:
        incorporation_date = date.fromisoformat(incorporation_date)

    accounts = raw.get("accounts", {})
    last_accounts = accounts.get("last_accounts", {})
    last_accounts_date = last_accounts.get("made_up_to")
    if last_accounts_date:
        last_accounts_date = date.fromisoformat(last_accounts_date)

    accounts_overdue = accounts.get("overdue", False)

    return {
        "company_number": raw.get("company_number", ""),
        "company_name": raw.get("company_name", ""),
        "company_status": raw.get("company_status", ""),
        "incorporation_date": incorporation_date,
        "sic_codes": raw.get("sic_codes", []),
        "registered_office_address": raw.get("registered_office_address", {}),
        "last_accounts_date": last_accounts_date,
        "accounts_overdue": accounts_overdue,
        "has_charges": raw.get("has_charges", False),
        "country": "UK",
    }


def ingest_company_batch(conn: Any, companies: list[dict]) -> int:
    """Insert or update a batch of normalized company records.

    Inserts into the companies table. Skips duplicates via ON CONFLICT.
    """
    if not companies:
        return 0

    rows = [
        (
            c["company_name"],
            c["country"],
            ",".join(c.get("sic_codes", [])) or None,
            c.get("sic_codes", [None])[0] if c.get("sic_codes") else None,
            c.get("incorporation_date"),
            "companies_house",
            c["company_number"],
            c.get("company_status"),
        )
        for c in companies
    ]

    query = """
        INSERT INTO companies (
            name, country, sector, sic_code,
            founding_date, source, source_id, current_status
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (source, source_id) WHERE source_id IS NOT NULL DO UPDATE SET
            current_status = EXCLUDED.current_status,
            name = EXCLUDED.name
    """
    return execute_many(conn, query, rows)


def get_verified_company_numbers(conn: Any) -> set[str]:
    """Return company numbers that have already been verified/ingested."""
    rows = execute_query(
        conn,
        "SELECT source_id FROM companies WHERE source = 'companies_house'",
    )
    return {r["source_id"] for r in rows}


def run_companies_house_pipeline(
    conn: Any,
    settings: Settings,
    company_numbers: list[str],
    skip_verified: bool = True,
) -> dict[str, int]:
    """Fetch and ingest company data for a list of company numbers.

    Parameters
    ----------
    conn:
        Database connection.
    settings:
        App settings with companies_house_api_key.
    company_numbers:
        List of Companies House company numbers to process.
    skip_verified:
        If True, skip companies already in the database.

    Returns
    -------
    dict
        Stats: {fetched, skipped, errors, ingested}.
    """
    stats = {"fetched": 0, "skipped": 0, "errors": 0, "ingested": 0}

    if skip_verified:
        verified = get_verified_company_numbers(conn)
        to_process = [cn for cn in company_numbers if cn not in verified]
        stats["skipped"] = len(company_numbers) - len(to_process)
    else:
        to_process = company_numbers

    if not to_process:
        return stats

    client = _make_client(settings.companies_house_api_key)
    batch: list[dict] = []

    for company_number in to_process:
        time.sleep(REQUEST_INTERVAL)
        profile = fetch_company_profile(client, company_number)
        if profile is None:
            stats["errors"] += 1
            continue

        normalized = normalize_company_profile(profile)
        batch.append(normalized)
        stats["fetched"] += 1

        if len(batch) >= 50:
            stats["ingested"] += ingest_company_batch(conn, batch)
            batch = []

    if batch:
        stats["ingested"] += ingest_company_batch(conn, batch)

    client.close()
    logger.info("companies_house_pipeline_complete", **stats)
    return stats
