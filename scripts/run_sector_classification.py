#!/usr/bin/env python3
"""Classify DERA CF companies into sectors using Claude Haiku.

Batches company names into groups of 50, sends concurrent async requests
to Claude Haiku for sector classification, and bulk-updates the companies
table. Resumable: only processes companies with NULL sector.
"""

from __future__ import annotations

import asyncio
import json
import re

import anthropic
import structlog
import typer

from startuplens.config import get_settings
from startuplens.db import execute_query, get_connection

logger = structlog.get_logger(__name__)
app = typer.Typer()

# Sector labels matching existing Form D INDUSTRYGROUPTYPE values (lowercased)
SECTORS = [
    "agriculture",
    "banking and financial services",
    "biotechnology",
    "business services",
    "commercial",
    "computers",
    "construction",
    "energy",
    "health care",
    "insurance",
    "investing",
    "manufacturing",
    "other",
    "other banking and financial services",
    "other energy",
    "other health care",
    "other real estate",
    "other technology",
    "pharmaceuticals",
    "pooled investment fund",
    "real estate",
    "restaurants",
    "retailing",
    "technology",
    "telecommunications",
    "travel",
]

SECTORS_SET = {s.lower() for s in SECTORS}

_CLASSIFICATION_PROMPT = """\
Classify each company into one industry sector based only on its name.

ALLOWED SECTORS (use one of these exact strings):
{sectors}

RULES:
1. Use ONLY the sector labels listed above.
2. If the name gives no clear signal, use "other".
3. Respond with ONLY a JSON array of sector strings, one per company, \
in the same order as the input list. No explanation, no markdown fences.

COMPANIES (numbered list):
{names}

JSON array of {count} sector strings:"""


def _fetch_unclassified(conn) -> list[dict]:
    """Fetch sec_dera_cf companies with NULL sector."""
    return execute_query(
        conn,
        """
        SELECT id::text, name
        FROM companies
        WHERE source = 'sec_dera_cf' AND sector IS NULL
        ORDER BY name
        """,
    )


def _parse_response(text: str) -> list[str]:
    """Parse JSON array from API response, stripping markdown if present."""
    cleaned = text.strip()
    fence_match = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1).strip()
    # Find the JSON array in the response
    arr_match = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if arr_match:
        cleaned = arr_match.group(0)
    return json.loads(cleaned)


def _validate_sector(sector: str) -> str:
    """Return a valid sector label, defaulting to 'other'."""
    s = sector.strip().lower()
    return s if s in SECTORS_SET else "other"


async def _classify_batch(
    client: anthropic.AsyncAnthropic,
    batch: list[dict],
    semaphore: asyncio.Semaphore,
    batch_idx: int,
    max_retries: int = 3,
) -> list[tuple[str, str]]:
    """Classify one batch of company names. Returns [(company_id, sector), ...]."""
    async with semaphore:
        names = [row["name"] for row in batch]
        names_block = "\n".join(f"{i + 1}. {n}" for i, n in enumerate(names))
        prompt = _CLASSIFICATION_PROMPT.format(
            sectors=", ".join(SECTORS),
            names=names_block,
            count=len(names),
        )

        for attempt in range(max_retries):
            try:
                response = await client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=4096,
                    messages=[{"role": "user", "content": prompt}],
                )
                break
            except anthropic.RateLimitError:
                wait = 2 ** attempt
                logger.warning(
                    "rate_limited", batch=batch_idx, retry_in=wait,
                )
                await asyncio.sleep(wait)
            except Exception as e:
                logger.warning(
                    "api_call_failed",
                    batch=batch_idx,
                    attempt=attempt + 1,
                    error=str(e)[:200],
                )
                if attempt == max_retries - 1:
                    return []
                await asyncio.sleep(1)
        else:
            return []

        try:
            sectors = _parse_response(response.content[0].text)
        except (json.JSONDecodeError, IndexError, ValueError) as e:
            logger.warning(
                "json_parse_failed",
                batch=batch_idx,
                error=str(e)[:100],
                response=response.content[0].text[:200],
            )
            return []

        if not isinstance(sectors, list):
            logger.warning("unexpected_response_type", batch=batch_idx)
            return []

        # Positional matching: pair sectors with companies by index
        results: list[tuple[str, str]] = []
        for i, row in enumerate(batch):
            if i < len(sectors):
                results.append((row["id"], _validate_sector(sectors[i])))

        if len(sectors) != len(batch):
            logger.warning(
                "count_mismatch",
                batch=batch_idx,
                expected=len(batch),
                got=len(sectors),
            )

        return results


async def _run_classification(
    companies: list[dict],
    api_key: str,
    batch_size: int,
    max_concurrent: int,
) -> list[tuple[str, str]]:
    """Classify all companies concurrently."""
    client = anthropic.AsyncAnthropic(api_key=api_key)
    semaphore = asyncio.Semaphore(max_concurrent)

    batches = [
        companies[i: i + batch_size]
        for i in range(0, len(companies), batch_size)
    ]

    logger.info(
        "starting_classification",
        companies=len(companies),
        batches=len(batches),
        max_concurrent=max_concurrent,
    )

    tasks = [
        _classify_batch(client, batch, semaphore, i)
        for i, batch in enumerate(batches)
    ]

    all_results: list[tuple[str, str]] = []
    completed = 0
    for coro in asyncio.as_completed(tasks):
        batch_results = await coro
        all_results.extend(batch_results)
        completed += 1
        if completed % 50 == 0:
            logger.info("progress", completed=completed, total=len(batches))

    return all_results


def _update_sectors(conn, results: list[tuple[str, str]]) -> int:
    """Bulk UPDATE companies.sector and sic_code."""
    if not results:
        return 0

    chunk_size = 500
    updated = 0

    for i in range(0, len(results), chunk_size):
        chunk = results[i: i + chunk_size]
        with conn.cursor() as cur:
            cur.executemany(
                """
                UPDATE companies
                SET sector = %s, sic_code = %s
                WHERE id = %s::uuid AND source = 'sec_dera_cf'
                """,
                [(sector, sector, cid) for cid, sector in chunk],
            )
            updated += cur.rowcount

    conn.commit()
    return updated


@app.command()
def main(
    batch_size: int = typer.Option(50, help="Company names per API call"),
    max_concurrent: int = typer.Option(20, help="Max concurrent API calls"),
    dry_run: bool = typer.Option(False, help="Classify but don't update DB"),
) -> None:
    """Classify DERA CF companies into sectors using Claude Haiku."""
    settings = get_settings()
    if not settings.anthropic_api_key:
        logger.error("SL_ANTHROPIC_API_KEY not set")
        raise typer.Exit(1)

    conn = get_connection(settings)

    try:
        companies = _fetch_unclassified(conn)
        logger.info("fetched_unclassified", count=len(companies))

        if not companies:
            logger.info("no_companies_to_classify")
            return

        results = asyncio.run(
            _run_classification(
                companies, settings.anthropic_api_key, batch_size, max_concurrent,
            )
        )

        logger.info("classification_complete", classified=len(results))

        if dry_run:
            for cid, sector in results[:20]:
                name = next(
                    (c["name"] for c in companies if c["id"] == cid), "?"
                )
                logger.info("sample", name=name, sector=sector)
            return

        updated = _update_sectors(conn, results)
        logger.info("sectors_updated", count=updated)

        # Show distribution
        dist = execute_query(
            conn,
            """
            SELECT sector, COUNT(*) as cnt
            FROM companies
            WHERE source = 'sec_dera_cf' AND sector IS NOT NULL
            GROUP BY sector
            ORDER BY cnt DESC
            """,
        )
        for row in dist:
            logger.info(
                "sector_distribution", sector=row["sector"], count=row["cnt"],
            )

    finally:
        conn.close()


if __name__ == "__main__":
    app()
