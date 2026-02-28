"""SEC EDGAR Form C quarterly dataset pipeline.

Downloads quarterly Form C filings from SEC EDGAR EFTS (full-text search) index,
parses them into structured company/funding records, and ingests into the
companies + funding_rounds + crowdfunding_outcomes tables.

Rate limit: SEC requests 10 req/s max and a descriptive User-Agent header.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import structlog

if TYPE_CHECKING:
    import psycopg

    from startuplens.config import Settings

logger = structlog.get_logger(__name__)

# SEC EDGAR EFTS base URL for Form C filings index
_EDGAR_FULL_INDEX_URL = (
    "https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{quarter}/company.idx"
)
# Rate-limit: 10 requests per second max
_MIN_REQUEST_INTERVAL = 0.1  # seconds between requests

# Form C filing type identifiers in SEC index
_FORM_C_TYPES = frozenset({"C", "C-U", "C/A", "C-U/A", "C-AR", "C-AR/A", "C-TR"})

# Column mapping: SEC EDGAR index field -> our internal field name
_INDEX_COLUMNS = ("company_name", "form_type", "cik", "date_filed", "filename")

# Normalized field mapping from raw Form C record to our schema
_FIELD_MAP: dict[str, str] = {
    "company_name": "name",
    "company_conformed_name": "name",
    "cik": "source_id",
    "date_filed": "filing_date",
    "date_of_first_sale": "round_date",
    "total_offering_amount": "funding_target",
    "total_amount_sold": "amount_raised",
    "total_remaining": "amount_remaining",
    "issuer_state": "state",
    "issuer_industry": "sector",
    "filename": "filing_url",
}


class _RateLimiter:
    """Simple token-bucket rate limiter for SEC EDGAR requests."""

    def __init__(self, min_interval: float = _MIN_REQUEST_INTERVAL) -> None:
        self._min_interval = min_interval
        self._last_request_time: float = 0.0

    def wait(self) -> None:
        """Block until the next request is allowed."""
        now = time.monotonic()
        elapsed = now - self._last_request_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request_time = time.monotonic()


# Module-level rate limiter shared across all calls in a pipeline run
_rate_limiter = _RateLimiter()


def _build_client(settings: Settings) -> httpx.Client:
    """Build an httpx client with SEC-required User-Agent header."""
    return httpx.Client(
        headers={
            "User-Agent": settings.sec_user_agent,
            "Accept-Encoding": "gzip, deflate",
        },
        follow_redirects=True,
        timeout=30.0,
    )


def download_form_c_index(
    year: int,
    quarter: int,
    output_dir: Path,
    *,
    settings: Settings | None = None,
) -> Path:
    """Download the quarterly EDGAR company index file.

    Args:
        year: Filing year (e.g., 2023).
        quarter: Quarter number (1-4).
        output_dir: Directory to save downloaded index files.
        settings: Application settings (for User-Agent header).

    Returns:
        Path to the downloaded index file.

    Raises:
        httpx.HTTPStatusError: If the download fails.
        ValueError: If year/quarter are invalid.
    """
    if not (2016 <= year <= 2030):
        msg = f"Year must be between 2016 and 2030, got {year}"
        raise ValueError(msg)
    if quarter not in (1, 2, 3, 4):
        msg = f"Quarter must be 1-4, got {quarter}"
        raise ValueError(msg)

    if settings is None:
        from startuplens.config import get_settings

        settings = get_settings()

    url = _EDGAR_FULL_INDEX_URL.format(year=year, quarter=quarter)
    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / f"company_{year}_Q{quarter}.idx"

    if dest.exists():
        logger.info("index_file_exists", path=str(dest))
        return dest

    logger.info("downloading_sec_edgar_index", url=url)
    _rate_limiter.wait()

    client = _build_client(settings)
    try:
        resp = client.get(url)
        resp.raise_for_status()
        dest.write_text(resp.text, encoding="utf-8")
        logger.info("saved_index", path=str(dest), bytes=len(resp.text))
    finally:
        client.close()

    return dest


def parse_form_c_filings(index_path: Path) -> list[dict]:
    """Parse a quarterly EDGAR index file and extract Form C filings.

    The index file is a fixed-width text file with a header section followed
    by data rows. Each row contains: Company Name, Form Type, CIK, Date Filed,
    and Filename (path to the filing on EDGAR).

    Args:
        index_path: Path to the downloaded .idx file.

    Returns:
        List of dicts with keys matching _INDEX_COLUMNS, filtered to Form C types.
    """
    filings: list[dict] = []
    content = index_path.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines()

    # Find the start of data (after the dashed separator line)
    data_start = 0
    for i, line in enumerate(lines):
        if line.startswith("---"):
            data_start = i + 1
            break

    for line in lines[data_start:]:
        if not line.strip():
            continue

        # SEC index uses fixed-width columns; parse by splitting on multiple spaces
        # Format: Company Name | Form Type | CIK | Date Filed | Filename
        # The company name can contain spaces, so we parse from the right
        parts = line.rsplit(maxsplit=3)
        if len(parts) < 4:
            continue

        # Extract filename, date, CIK from the rightmost columns
        filename = parts[-1]
        date_filed = parts[-2]
        cik = parts[-3]

        # Everything before the CIK needs further splitting
        # Use find() (first occurrence) not rfind() because the CIK may appear in filename
        remaining = line[: line.find(cik)].rstrip()
        # Split remaining to separate company name from form type
        remaining_parts = remaining.rsplit(maxsplit=1)
        if len(remaining_parts) < 2:
            continue

        company_name = remaining_parts[0].strip()
        form_type = remaining_parts[1].strip()

        # Filter to Form C types only
        if form_type not in _FORM_C_TYPES:
            continue

        filings.append({
            "company_name": company_name,
            "form_type": form_type,
            "cik": cik.strip(),
            "date_filed": date_filed.strip(),
            "filename": filename.strip(),
        })

    logger.info("parsed_form_c_filings", count=len(filings), file=index_path.name)
    return filings


def normalize_form_c_record(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize a raw Form C record into our internal schema.

    Applies field name mapping, type coercion, and default values.

    Args:
        raw: Dict with keys from _INDEX_COLUMNS or XBRL-parsed fields.

    Returns:
        Normalized dict ready for database insertion.
    """
    normalized: dict[str, Any] = {}

    # Map known fields
    for raw_key, internal_key in _FIELD_MAP.items():
        if raw_key in raw and raw[raw_key] is not None:
            normalized[internal_key] = raw[raw_key]

    # Ensure required fields have defaults
    normalized.setdefault("name", "Unknown")
    normalized.setdefault("source", "sec_edgar")
    normalized.setdefault("country", "US")

    # Type coercion for numeric fields
    for numeric_field in ("funding_target", "amount_raised", "amount_remaining"):
        if numeric_field in normalized:
            val = normalized[numeric_field]
            if isinstance(val, str):
                # Remove currency symbols and commas
                cleaned = val.replace("$", "").replace(",", "").strip()
                try:
                    normalized[numeric_field] = float(cleaned) if cleaned else None
                except ValueError:
                    normalized[numeric_field] = None

    # Clean CIK to just digits
    if "source_id" in normalized:
        cik = str(normalized["source_id"]).strip()
        normalized["source_id"] = cik.lstrip("0") or "0"

    # Normalize sector
    if "sector" in normalized:
        sector = str(normalized["sector"]).strip().lower()
        normalized["sector"] = sector if sector else None

    # Derive form_type metadata
    if "form_type" in raw:
        normalized["form_type"] = raw["form_type"]

    return normalized


def _is_quarter_ingested(conn: psycopg.Connection, year: int, quarter: int) -> bool:
    """Check if a given quarter has already been ingested."""
    from startuplens.db import execute_query

    rows = execute_query(
        conn,
        "SELECT 1 FROM companies WHERE source = 'sec_edgar' AND source_id LIKE %s LIMIT 1",
        (f"%_q{year}Q{quarter}",),
    )
    return len(rows) > 0


def ingest_form_c_batch(conn: psycopg.Connection, records: list[dict]) -> int:
    """Insert normalized Form C records into companies + funding_rounds tables.

    Args:
        conn: Database connection.
        records: List of normalized dicts from normalize_form_c_record().

    Returns:
        Number of records inserted.
    """
    if not records:
        return 0

    inserted = 0

    for rec in records:
        # Insert into companies table (upsert on source + source_id)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO companies (name, country, sector, source, source_id, sic_code)
                VALUES (%(name)s, %(country)s, %(sector)s, %(source)s, %(source_id)s, %(sic_code)s)
                ON CONFLICT (source, source_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    sector = EXCLUDED.sector
                RETURNING id
                """,
                {
                    "name": rec.get("name"),
                    "country": rec.get("country", "US"),
                    "sector": rec.get("sector"),
                    "source": "sec_edgar",
                    "source_id": rec.get("source_id", ""),
                    "sic_code": rec.get("sic_code"),
                },
            )
            row = cur.fetchone()
            company_id = row["id"] if row else None

            if company_id is None:
                continue

            # Insert funding round if we have financial data
            if rec.get("funding_target") or rec.get("amount_raised"):
                cur.execute(
                    """
                    INSERT INTO funding_rounds (
                        company_id, round_date, round_type, instrument_type,
                        amount_raised, pre_money_valuation, platform, source
                    ) VALUES (
                        %(company_id)s, %(round_date)s, %(round_type)s,
                        %(instrument_type)s, %(amount_raised)s,
                        %(pre_money_valuation)s, %(platform)s, %(source)s
                    )
                    """,
                    {
                        "company_id": company_id,
                        "round_date": rec.get("filing_date") or rec.get("round_date"),
                        "round_type": _classify_round_type(rec.get("form_type")),
                        "instrument_type": _classify_instrument_type(rec.get("form_type")),
                        "amount_raised": rec.get("amount_raised"),
                        "pre_money_valuation": rec.get("pre_money_valuation"),
                        "platform": rec.get("platform"),
                        "source": "sec_edgar",
                    },
                )

            inserted += 1

    conn.commit()
    logger.info("ingested_form_c_batch", inserted=inserted)
    return inserted


def _classify_round_type(form_type: str | None) -> str:
    """Map SEC form type to our round_type taxonomy."""
    if form_type in ("C", "C-U"):
        return "reg_cf"
    if form_type in ("C/A", "C-U/A"):
        return "reg_cf_amendment"
    if form_type == "C-AR":
        return "reg_cf_annual_report"
    if form_type == "C-TR":
        return "reg_cf_termination"
    return "reg_cf"


def _classify_instrument_type(form_type: str | None) -> str:
    """Default instrument type for Reg CF filings.

    Most Reg CF offerings are equity or SAFE; without parsing the full filing
    we default to equity and refine later.
    """
    return "equity"


def run_sec_pipeline(
    conn: psycopg.Connection,
    settings: Settings,
    years: list[int],
    *,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Orchestrate the full SEC EDGAR Form C pipeline.

    Downloads quarterly index files for the given years, parses Form C filings,
    normalizes records, and ingests them into the database. Skips quarters
    that have already been ingested (resumable).

    Args:
        conn: Database connection.
        settings: Application settings.
        years: List of years to process (e.g., [2021, 2022, 2023]).
        output_dir: Directory for downloaded files. Defaults to ./data/sec_edgar.

    Returns:
        Summary dict with counts of downloaded, parsed, and ingested records.
    """
    if output_dir is None:
        output_dir = Path("data/sec_edgar")

    summary: dict[str, Any] = {
        "years": years,
        "quarters_processed": 0,
        "quarters_skipped": 0,
        "filings_parsed": 0,
        "records_ingested": 0,
        "errors": [],
    }

    for year in years:
        for quarter in (1, 2, 3, 4):
            quarter_key = f"{year}-Q{quarter}"

            # Resumability: skip already-ingested quarters
            if _is_quarter_ingested(conn, year, quarter):
                logger.info("skipping_ingested_quarter", quarter=quarter_key)
                summary["quarters_skipped"] += 1
                continue

            try:
                # Step 1: Download index
                index_path = download_form_c_index(
                    year, quarter, output_dir, settings=settings
                )

                # Step 2: Parse Form C filings from index
                raw_filings = parse_form_c_filings(index_path)
                summary["filings_parsed"] += len(raw_filings)

                # Step 3: Normalize each record
                normalized = [normalize_form_c_record(f) for f in raw_filings]

                # Tag records with quarter for resumability tracking
                for rec in normalized:
                    if rec.get("source_id"):
                        rec["source_id"] = f"{rec['source_id']}_q{year}Q{quarter}"

                # Step 4: Ingest batch
                count = ingest_form_c_batch(conn, normalized)
                summary["records_ingested"] += count
                summary["quarters_processed"] += 1

                logger.info(
                    "quarter_complete",
                    quarter=quarter_key,
                    filings=len(raw_filings),
                    records=count,
                )

            except httpx.HTTPStatusError as e:
                error_msg = f"{quarter_key}: HTTP {e.response.status_code}"
                logger.warning("quarter_http_error", quarter=quarter_key, error=error_msg)
                summary["errors"].append(error_msg)
            except Exception as e:
                error_msg = f"{quarter_key}: {e!s}"
                logger.warning("quarter_error", quarter=quarter_key, error=error_msg)
                summary["errors"].append(error_msg)

    logger.info(
        "sec_pipeline_complete",
        quarters_processed=summary["quarters_processed"],
        records_ingested=summary["records_ingested"],
        errors=len(summary["errors"]),
    )
    return summary
