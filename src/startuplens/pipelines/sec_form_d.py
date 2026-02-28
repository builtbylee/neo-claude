"""SEC EDGAR Form D (Reg D) quarterly dataset pipeline.

Downloads quarterly Form D datasets from SEC structured data,
parses tab-delimited files (ISSUERS, OFFERINGS, FORMDSUBMISSION),
normalizes records, and ingests into companies + funding_rounds tables.

Data source: https://www.sec.gov/data-research/sec-markets-data/form-d-data-sets
Rate limit: SEC requests 10 req/s max and a descriptive User-Agent header.
"""

from __future__ import annotations

import csv
import io
import time
import zipfile
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import structlog

if TYPE_CHECKING:
    import psycopg

    from startuplens.config import Settings

logger = structlog.get_logger(__name__)

# SEC structured data URL for Form D quarterly datasets
_FORM_D_DATASET_URL = (
    "https://www.sec.gov/files/structureddata/data/form-d-data-sets/"
    "{year}q{quarter}_d.zip"
)

# Rate-limit: 10 requests per second max
_MIN_REQUEST_INTERVAL = 0.1

# TSV files we extract from each quarterly ZIP
_SUBMISSION_FILE = "FORMDSUBMISSION.tsv"
_ISSUERS_FILE = "ISSUERS.tsv"
_OFFERINGS_FILE = "OFFERING.tsv"  # Note: singular in the actual ZIP

# Batch commit size for large quarterly datasets
_BATCH_COMMIT_SIZE = 1000


class _RateLimiter:
    """Simple rate limiter for SEC requests."""

    def __init__(self, min_interval: float = _MIN_REQUEST_INTERVAL) -> None:
        self._min_interval = min_interval
        self._last_request_time: float = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_request_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request_time = time.monotonic()


_rate_limiter = _RateLimiter()


def _build_client(settings: Settings) -> httpx.Client:
    """Build an httpx client with SEC-required User-Agent header."""
    return httpx.Client(
        headers={
            "User-Agent": settings.sec_user_agent,
            "Accept-Encoding": "gzip, deflate",
        },
        follow_redirects=True,
        timeout=60.0,
    )


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


def download_form_d_dataset(
    year: int,
    quarter: int,
    output_dir: Path,
    *,
    settings: Settings | None = None,
) -> Path:
    """Download a quarterly Form D dataset ZIP from SEC.

    Args:
        year: Filing year (2009-2030).
        quarter: Quarter number (1-4).
        output_dir: Directory to save downloaded ZIP files.
        settings: Application settings (for User-Agent header).

    Returns:
        Path to the downloaded ZIP file.
    """
    if not (2009 <= year <= 2030):
        msg = f"Year must be between 2009 and 2030, got {year}"
        raise ValueError(msg)
    if quarter not in (1, 2, 3, 4):
        msg = f"Quarter must be 1-4, got {quarter}"
        raise ValueError(msg)

    if settings is None:
        from startuplens.config import get_settings

        settings = get_settings()

    url = _FORM_D_DATASET_URL.format(year=year, quarter=quarter)
    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / f"form_d_{year}_Q{quarter}.zip"

    if dest.exists():
        logger.info("form_d_zip_exists", path=str(dest))
        return dest

    logger.info("downloading_form_d_dataset", url=url)
    _rate_limiter.wait()

    client = _build_client(settings)
    try:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in resp.iter_bytes():
                    f.write(chunk)
        logger.info("saved_form_d_zip", path=str(dest))
    finally:
        client.close()

    return dest


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------


def _read_tsv_from_zip(
    zf: zipfile.ZipFile, filename: str,
) -> list[dict[str, str]]:
    """Read a TSV file from a ZIP archive, normalizing column names to uppercase."""
    # Find the file in the ZIP (may have path prefix or case variation)
    matching = [n for n in zf.namelist() if n.upper().endswith(filename.upper())]
    if not matching:
        return []

    raw = zf.read(matching[0])
    # Try UTF-8 first, fall back to Latin-1
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    rows = []
    for row in reader:
        # Normalize keys to uppercase
        normalized = {k.upper().strip(): v.strip() if v else "" for k, v in row.items() if k}
        rows.append(normalized)
    return rows


def parse_form_d_dataset(zip_path: Path) -> list[dict]:
    """Parse a quarterly Form D ZIP dataset.

    Joins FORMDSUBMISSION, ISSUERS, and OFFERINGS on ACCESSIONNUMBER.
    Returns one record per (ACCESSIONNUMBER, CIK) pair.
    """
    with zipfile.ZipFile(zip_path, "r") as zf:
        submissions = _read_tsv_from_zip(zf, _SUBMISSION_FILE)
        issuers = _read_tsv_from_zip(zf, _ISSUERS_FILE)
        offerings = _read_tsv_from_zip(zf, _OFFERINGS_FILE)

    # Index offerings by ACCESSIONNUMBER
    offering_by_accession: dict[str, dict] = {}
    for off in offerings:
        acc = off.get("ACCESSIONNUMBER", "")
        if acc:
            offering_by_accession[acc] = off

    # Index submissions by ACCESSIONNUMBER
    submission_by_accession: dict[str, dict] = {}
    for sub in submissions:
        acc = sub.get("ACCESSIONNUMBER", "")
        if acc:
            submission_by_accession[acc] = sub

    # Join: one record per (ACCESSIONNUMBER, CIK) from ISSUERS
    records: list[dict] = []
    for issuer in issuers:
        acc = issuer.get("ACCESSIONNUMBER", "")
        if not acc:
            continue

        record = dict(issuer)

        # Merge submission fields
        sub = submission_by_accession.get(acc, {})
        for key in ("FILING_DATE", "SUBMISSIONTYPE", "SIC_CODE"):
            if key in sub and key not in record:
                record[key] = sub[key]

        # Merge offering fields
        off = offering_by_accession.get(acc, {})
        for key in (
            "TOTALOFFERINGAMOUNT", "TOTALAMOUNTSOLD", "TOTALREMAINING",
            "SALE_DATE", "FEDERALEXEMPTIONS_ITEMS_LIST", "INDUSTRYGROUPTYPE",
        ):
            if key in off:
                record[key] = off[key]

        records.append(record)

    logger.info("parsed_form_d_dataset", count=len(records), file=zip_path.name)
    return records


# ---------------------------------------------------------------------------
# Normalize
# ---------------------------------------------------------------------------

_FIELD_MAP: dict[str, str] = {
    "ENTITYNAME": "name",
    "CIK": "source_id",
    # Submission fields
    "FILING_DATE": "filing_date",
    # Offering fields
    "SALE_DATE": "round_date",
    "TOTALOFFERINGAMOUNT": "funding_target",
    "TOTALAMOUNTSOLD": "amount_raised",
    "TOTALREMAINING": "amount_remaining",
    "FEDERALEXEMPTIONS_ITEMS_LIST": "federal_exemptions",
    "INDUSTRYGROUPTYPE": "sector",
    # Issuer fields
    "YEAROFINC_VALUE_ENTERED": "founding_year",
    "JURISDICTIONOFINC": "state",
}


def normalize_form_d_record(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize a raw Form D record into our internal schema."""
    normalized: dict[str, Any] = {}

    for raw_key, internal_key in _FIELD_MAP.items():
        if raw_key in raw and raw[raw_key]:
            normalized[internal_key] = raw[raw_key]

    # Defaults
    normalized.setdefault("name", "Unknown")
    normalized.setdefault("source", "sec_form_d")
    normalized.setdefault("country", "US")

    # Clean CIK: strip leading zeros
    if "source_id" in normalized:
        cik = str(normalized["source_id"]).strip()
        normalized["source_id"] = cik.lstrip("0") or "0"

    # Normalize filing_date (may be DD-MON-YYYY format like "29-MAR-2024")
    if "filing_date" in normalized:
        fd = str(normalized["filing_date"]).strip()
        if fd and "-" in fd and not fd[0].isdigit():
            # Already ISO format like 2024-03-29
            pass
        elif fd:
            try:
                from datetime import datetime
                normalized["filing_date"] = datetime.strptime(fd, "%d-%b-%Y").strftime("%Y-%m-%d")
            except ValueError:
                pass  # Keep as-is if we can't parse

    # Numeric coercion for financial fields
    for field in ("funding_target", "amount_raised", "amount_remaining"):
        if field in normalized:
            val = str(normalized[field])
            # Handle "Indefinite" and other non-numeric values
            cleaned = val.replace("$", "").replace(",", "").strip()
            try:
                normalized[field] = float(cleaned) if cleaned else None
            except ValueError:
                normalized[field] = None

    # Derive founding_date from year of incorporation
    founding_year = normalized.pop("founding_year", None)
    if founding_year:
        try:
            year = int(str(founding_year).strip())
            if 1800 <= year <= 2030:
                normalized["founding_date"] = date(year, 1, 1)
        except (ValueError, TypeError):
            pass
    normalized.setdefault("founding_date", None)

    # Normalize sector
    if "sector" in normalized:
        sector = str(normalized["sector"]).strip().lower()
        normalized["sector"] = sector if sector else None

    # Preserve submission type
    if "SUBMISSIONTYPE" in raw:
        normalized["submission_type"] = raw["SUBMISSIONTYPE"]

    return normalized


# ---------------------------------------------------------------------------
# Round type classification
# ---------------------------------------------------------------------------


def _classify_round_type_d(federal_exemptions: str | None) -> str:
    """Map Form D federal exemptions to our round_type taxonomy."""
    if not federal_exemptions:
        return "reg_d"

    exemptions = federal_exemptions.lower()

    # 506(c) takes priority (general solicitation, more significant)
    if "06c" in exemptions:
        return "rule_506c"
    if "06b" in exemptions:
        return "rule_506b"
    if "04" in exemptions and "06" not in exemptions:
        return "rule_504"
    if "4(a)(5)" in exemptions or "4a5" in exemptions:
        return "section_4a5"
    # Generic 506 (unspecified b or c)
    if "06" in exemptions:
        return "rule_506"
    return "reg_d"


# ---------------------------------------------------------------------------
# Resumability check
# ---------------------------------------------------------------------------


def _is_quarter_ingested_d(conn: psycopg.Connection, year: int, quarter: int) -> bool:
    """Check if a given quarter has already been ingested for Form D."""
    from startuplens.db import execute_query

    rows = execute_query(
        conn,
        "SELECT 1 FROM companies WHERE source = 'sec_form_d' AND source_id LIKE %s LIMIT 1",
        (f"%_q{year}Q{quarter}",),
    )
    return len(rows) > 0


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------


def ingest_form_d_batch(conn: psycopg.Connection, records: list[dict]) -> int:
    """Insert normalized Form D records into companies + funding_rounds tables.

    Uses bulk multi-row INSERTs for performance (500 rows per query instead of
    one round-trip per record). Two-phase: bulk insert companies, then bulk
    insert funding rounds using the returned company IDs.

    Returns number of records inserted.
    """
    if not records:
        return 0

    chunk_size = 500
    inserted = 0

    for chunk_start in range(0, len(records), chunk_size):
        chunk = records[chunk_start : chunk_start + chunk_size]

        # Phase 1: Bulk upsert companies
        # Deduplicate by source_id within chunk — same CIK can appear
        # multiple times in a quarter (amendments, multi-issuer filings).
        # ON CONFLICT can't update the same row twice in one statement.
        seen: dict[str, tuple] = {}
        for rec in chunk:
            sid = rec.get("source_id", "")
            seen[sid] = (
                rec.get("name"),
                rec.get("country", "US"),
                rec.get("sector"),
                rec.get("sector"),  # sic_code = industry group
                rec.get("founding_date"),
                "sec_form_d",
                sid,
            )
        company_rows = list(seen.values())

        # Build multi-row VALUES clause
        placeholders = ", ".join(
            ["(%s, %s, %s, %s, %s, %s, %s)"] * len(company_rows),
        )
        flat_values = [v for row in company_rows for v in row]

        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO companies (name, country, sector, sic_code,
                    founding_date, source, source_id)
                VALUES {placeholders}
                ON CONFLICT (source, source_id) WHERE source_id IS NOT NULL
                DO UPDATE SET
                    name = EXCLUDED.name,
                    sector = EXCLUDED.sector
                RETURNING id, source_id
                """,
                flat_values,
            )
            returned = cur.fetchall()

        # Build source_id -> company_id lookup
        id_by_source = {row["source_id"]: row["id"] for row in returned}

        # Phase 2: Bulk insert funding rounds
        funding_rows = []
        for rec in chunk:
            sid = rec.get("source_id", "")
            company_id = id_by_source.get(sid)
            if company_id is None:
                continue
            if not (rec.get("funding_target") or rec.get("amount_raised")):
                continue
            funding_rows.append((
                company_id,
                rec.get("filing_date") or rec.get("round_date"),
                _classify_round_type_d(rec.get("federal_exemptions")),
                "equity",
                rec.get("amount_raised"),
                None,
                None,
                "sec_form_d",
            ))

        if funding_rows:
            fr_placeholders = ", ".join(
                ["(%s, %s, %s, %s, %s, %s, %s, %s)"] * len(funding_rows),
            )
            fr_flat = [v for row in funding_rows for v in row]
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO funding_rounds (
                        company_id, round_date, round_type, instrument_type,
                        amount_raised, pre_money_valuation, platform, source
                    ) VALUES {fr_placeholders}
                    """,
                    fr_flat,
                )

        inserted += len(returned)
        conn.commit()

    logger.info("ingested_form_d_batch", inserted=inserted)
    return inserted


# ---------------------------------------------------------------------------
# CIK cross-referencing
# ---------------------------------------------------------------------------


def cross_reference_sec_filings(conn: psycopg.Connection) -> dict[str, int]:
    """Link Form C and Form D companies that share the same CIK.

    Finds companies with matching CIK numbers across sec_edgar and sec_form_d
    sources and links them to the same canonical entity.

    Returns dict with counts: {ciks_matched, entities_linked, already_linked}.
    """
    from startuplens.db import execute_query
    from startuplens.entity_resolution.resolver import resolve_entity

    # Find CIKs that appear in both Form C and Form D
    rows = execute_query(
        conn,
        """
        SELECT DISTINCT
            SPLIT_PART(fc.source_id, '_q', 1) AS cik,
            fc.name AS form_c_name,
            fc.country AS form_c_country,
            fd.name AS form_d_name,
            fd.country AS form_d_country,
            fd.id::text AS form_d_company_id
        FROM companies fc
        JOIN companies fd
            ON SPLIT_PART(fc.source_id, '_q', 1) = SPLIT_PART(fd.source_id, '_q', 1)
        WHERE fc.source = 'sec_edgar'
          AND fd.source = 'sec_form_d'
          AND fd.entity_id IS NULL
        """,
    )

    stats = {"ciks_matched": 0, "entities_linked": 0, "already_linked": 0}

    seen_ciks: set[str] = set()
    for row in rows:
        cik = row["cik"]
        if cik in seen_ciks:
            continue
        seen_ciks.add(cik)
        stats["ciks_matched"] += 1

        # Use entity resolution to link the Form D record
        resolve_entity(
            conn,
            name=row["form_d_name"],
            country=row["form_d_country"] or "US",
            source="sec_form_d",
            source_identifier=row["form_d_company_id"],
        )
        stats["entities_linked"] += 1

    logger.info("cross_referenced_sec_filings", **stats)
    return stats


# ---------------------------------------------------------------------------
# Pipeline orchestrator
# ---------------------------------------------------------------------------


def _process_quarter(
    settings: Settings,
    year: int,
    quarter: int,
    output_dir: Path,
) -> dict[str, Any]:
    """Process a single quarter: download, parse, normalize, ingest.

    Opens its own DB connection so it can run in a thread safely.
    """
    from startuplens.db import get_connection

    quarter_key = f"{year}-Q{quarter}"
    conn = get_connection(settings)

    try:
        if _is_quarter_ingested_d(conn, year, quarter):
            logger.info("skipping_ingested_quarter_d", quarter=quarter_key)
            return {"status": "skipped", "quarter": quarter_key}

        zip_path = download_form_d_dataset(
            year, quarter, output_dir, settings=settings,
        )

        raw_records = parse_form_d_dataset(zip_path)
        normalized = [normalize_form_d_record(r) for r in raw_records]

        for rec in normalized:
            if rec.get("source_id"):
                rec["source_id"] = f"{rec['source_id']}_q{year}Q{quarter}"

        count = ingest_form_d_batch(conn, normalized)

        logger.info(
            "quarter_complete_d",
            quarter=quarter_key,
            filings=len(raw_records),
            records=count,
        )
        return {
            "status": "ok",
            "quarter": quarter_key,
            "filings": len(raw_records),
            "records": count,
        }

    except httpx.HTTPStatusError as e:
        error_msg = f"{quarter_key}: HTTP {e.response.status_code}"
        logger.warning("quarter_http_error_d", quarter=quarter_key, error=error_msg)
        return {"status": "error", "quarter": quarter_key, "error": error_msg}
    except Exception as e:
        error_msg = f"{quarter_key}: {e!s}"
        logger.warning("quarter_error_d", quarter=quarter_key, error=error_msg)
        return {"status": "error", "quarter": quarter_key, "error": error_msg}
    finally:
        conn.close()


def run_form_d_pipeline(
    conn: psycopg.Connection,
    settings: Settings,
    years: list[int],
    *,
    output_dir: Path | None = None,
    max_workers: int = 4,
) -> dict[str, Any]:
    """Orchestrate the full SEC Form D pipeline with concurrent quarters.

    Downloads quarterly ZIP datasets for the given years, parses filings,
    normalizes records, and ingests into the database. Skips quarters
    that have already been ingested (resumable).

    Each quarter runs in its own thread with its own DB connection for
    maximum throughput. The ``conn`` argument is only used for the summary
    query at the end.

    Args:
        conn: Database connection (used for final summary only).
        settings: Application settings.
        years: List of years to process.
        output_dir: Directory for downloaded files.
        max_workers: Number of concurrent threads (default 4).

    Returns:
        Summary dict with counts.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    if output_dir is None:
        output_dir = Path("data/sec_form_d")

    summary: dict[str, Any] = {
        "years": years,
        "quarters_processed": 0,
        "quarters_skipped": 0,
        "filings_parsed": 0,
        "records_ingested": 0,
        "errors": [],
    }

    # Build list of all (year, quarter) pairs
    quarters = [(y, q) for y in years for q in (1, 2, 3, 4)]

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_process_quarter, settings, year, quarter, output_dir): (year, quarter)
            for year, quarter in quarters
        }

        for future in as_completed(futures):
            result = future.result()
            if result["status"] == "skipped":
                summary["quarters_skipped"] += 1
            elif result["status"] == "ok":
                summary["quarters_processed"] += 1
                summary["filings_parsed"] += result.get("filings", 0)
                summary["records_ingested"] += result.get("records", 0)
            else:
                summary["errors"].append(result.get("error", "unknown"))

    logger.info(
        "form_d_pipeline_complete",
        quarters_processed=summary["quarters_processed"],
        records_ingested=summary["records_ingested"],
        errors=len(summary["errors"]),
    )
    return summary
