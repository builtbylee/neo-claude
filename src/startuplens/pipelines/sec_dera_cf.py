"""SEC DERA Crowdfunding Offerings Data Sets pipeline.

Downloads quarterly crowdfunding datasets from SEC DERA, parses TSV files
(FORM_C_SUBMISSION, FORM_C_ISSUER_INFORMATION, FORM_C_DISCLOSURE),
normalizes records, and ingests into companies + funding_rounds +
crowdfunding_outcomes + financial_data tables.

Data source: https://www.sec.gov/files/dera/data/crowdfunding-offerings-data-sets/
Rate limit: SEC requests 10 req/s max and a descriptive User-Agent header.
"""

from __future__ import annotations

import csv
import io
import time
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import structlog

if TYPE_CHECKING:
    import psycopg

    from startuplens.config import Settings

logger = structlog.get_logger(__name__)

# SEC DERA crowdfunding dataset URL
_DERA_CF_URL = (
    "https://www.sec.gov/files/dera/data/"
    "crowdfunding-offerings-data-sets/{year}q{quarter}_cf.zip"
)

# Rate-limit: 10 requests per second max
_MIN_REQUEST_INTERVAL = 0.1

# TSV files we join from each quarterly ZIP
_SUBMISSION_FILE = "FORM_C_SUBMISSION.tsv"
_ISSUER_FILE = "FORM_C_ISSUER_INFORMATION.tsv"
_DISCLOSURE_FILE = "FORM_C_DISCLOSURE.tsv"


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


def download_dera_cf_dataset(
    year: int,
    quarter: int,
    output_dir: Path,
    *,
    settings: Settings | None = None,
) -> Path:
    """Download a quarterly DERA crowdfunding dataset ZIP from SEC.

    Returns:
        Path to the downloaded ZIP file.
    """
    if not (2017 <= year <= 2030):
        msg = f"Year must be between 2017 and 2030, got {year}"
        raise ValueError(msg)
    if quarter not in (1, 2, 3, 4):
        msg = f"Quarter must be 1-4, got {quarter}"
        raise ValueError(msg)

    if settings is None:
        from startuplens.config import get_settings

        settings = get_settings()

    url = _DERA_CF_URL.format(year=year, quarter=quarter)
    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / f"dera_cf_{year}_Q{quarter}.zip"

    if dest.exists():
        logger.info("dera_cf_zip_exists", path=str(dest))
        return dest

    logger.info("downloading_dera_cf_dataset", url=url)
    _rate_limiter.wait()

    client = _build_client(settings)
    try:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in resp.iter_bytes():
                    f.write(chunk)
        logger.info("saved_dera_cf_zip", path=str(dest))
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
    matching = [n for n in zf.namelist() if n.upper().endswith(filename.upper())]
    if not matching:
        return []

    raw = zf.read(matching[0])
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    rows = []
    for row in reader:
        normalized = {
            k.upper().strip(): v.strip() if v else ""
            for k, v in row.items()
            if k
        }
        rows.append(normalized)
    return rows


def parse_dera_cf_dataset(zip_path: Path) -> list[dict]:
    """Parse a quarterly DERA CF ZIP dataset.

    Joins FORM_C_SUBMISSION, FORM_C_ISSUER_INFORMATION, and FORM_C_DISCLOSURE
    on ACCESSION_NUMBER. Returns one record per filing.
    """
    with zipfile.ZipFile(zip_path, "r") as zf:
        submissions = _read_tsv_from_zip(zf, _SUBMISSION_FILE)
        issuers = _read_tsv_from_zip(zf, _ISSUER_FILE)
        disclosures = _read_tsv_from_zip(zf, _DISCLOSURE_FILE)

    # Index by ACCESSION_NUMBER
    issuer_by_acc: dict[str, dict] = {}
    for iss in issuers:
        acc = iss.get("ACCESSION_NUMBER", "")
        if acc:
            issuer_by_acc[acc] = iss

    disclosure_by_acc: dict[str, dict] = {}
    for disc in disclosures:
        acc = disc.get("ACCESSION_NUMBER", "")
        if acc:
            disclosure_by_acc[acc] = disc

    # Join on submission (primary key)
    records: list[dict] = []
    for sub in submissions:
        acc = sub.get("ACCESSION_NUMBER", "")
        if not acc:
            continue

        record = dict(sub)
        issuer = issuer_by_acc.get(acc, {})
        disclosure = disclosure_by_acc.get(acc, {})

        for k, v in issuer.items():
            if k != "ACCESSION_NUMBER":
                record[k] = v
        for k, v in disclosure.items():
            if k != "ACCESSION_NUMBER":
                record[k] = v

        records.append(record)

    logger.info("parsed_dera_cf_dataset", count=len(records), file=zip_path.name)
    return records


# ---------------------------------------------------------------------------
# Normalize
# ---------------------------------------------------------------------------


def _safe_float(val: str | None) -> float | None:
    """Parse a numeric string, returning None on failure."""
    if not val:
        return None
    cleaned = val.replace("$", "").replace(",", "").strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _safe_int(val: str | None) -> int | None:
    """Parse an integer string, returning None on failure."""
    if not val:
        return None
    cleaned = val.replace(",", "").strip()
    if not cleaned:
        return None
    try:
        return int(float(cleaned))
    except ValueError:
        return None


def _parse_date(val: str | None) -> str | None:
    """Parse date string in various formats, return ISO format or None."""
    if not val or not val.strip():
        return None
    val = val.strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%m/%d/%Y", "%d-%b-%Y"):
        try:
            from datetime import datetime

            return datetime.strptime(val, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def normalize_dera_cf_record(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize a raw DERA CF record into our internal schema."""
    result: dict[str, Any] = {
        "source": "sec_dera_cf",
        "country": "US",
    }

    # Company info
    result["name"] = raw.get("NAMEOFISSUER", "").strip() or "Unknown"
    result["cik"] = (raw.get("CIK", "") or "").strip().lstrip("0") or "0"

    # State/country
    state = (raw.get("STATEORCOUNTRY", "") or "").strip()
    if state and len(state) > 2:
        result["country"] = state
    elif state:
        result["state"] = state

    # Dates
    result["filing_date"] = _parse_date(raw.get("FILING_DATE"))
    result["date_incorporation"] = _parse_date(raw.get("DATEINCORPORATION"))

    # Offering details
    result["offering_amount"] = _safe_float(raw.get("OFFERINGAMOUNT"))
    result["max_offering_amount"] = _safe_float(raw.get("MAXIMUMOFFERINGAMOUNT"))
    result["security_type"] = (raw.get("SECURITYOFFEREDTYPE", "") or "").strip()
    result["price"] = _safe_float(raw.get("PRICE"))
    result["deadline_date"] = _parse_date(raw.get("DEADLINEDATE"))
    result["oversubscription_accepted"] = (
        raw.get("OVERSUBSCRIPTIONACCEPTED", "").strip().upper() == "Y"
    )

    # Financials — most recent fiscal year
    result["employees"] = _safe_int(raw.get("CURRENTEMPLOYEES"))
    result["total_assets_recent"] = _safe_float(raw.get("TOTALASSETMOSTRECENTFISCALYEAR"))
    result["total_assets_prior"] = _safe_float(raw.get("TOTALASSETPRIORFISCALYEAR"))
    result["cash_recent"] = _safe_float(raw.get("CASHEQUIMOSTRECENTFISCALYEAR"))
    result["cash_prior"] = _safe_float(raw.get("CASHEQUIPRIORFISCALYEAR"))
    result["revenue_recent"] = _safe_float(raw.get("REVENUEMOSTRECENTFISCALYEAR"))
    result["revenue_prior"] = _safe_float(raw.get("REVENUEPRIORFISCALYEAR"))
    result["cogs_recent"] = _safe_float(raw.get("COSTGOODSSOLDRECENTFISCALYEAR"))
    result["cogs_prior"] = _safe_float(raw.get("COSTGOODSSOLDPRIORFISCALYEAR"))
    result["net_income_recent"] = _safe_float(raw.get("NETINCOMEMOSTRECENTFISCALYEAR"))
    result["net_income_prior"] = _safe_float(raw.get("NETINCOMEPRIORFISCALYEAR"))
    result["short_term_debt_recent"] = _safe_float(
        raw.get("SHORTTERMDEBTMRECENTFISCALYEAR"),
    )
    result["long_term_debt_recent"] = _safe_float(
        raw.get("LONGTERMDEBTRECENTFISCALYEAR"),
    )
    result["short_term_debt_prior"] = _safe_float(
        raw.get("SHORTTERMDEBTPRIORFISCALYEAR"),
    )
    result["long_term_debt_prior"] = _safe_float(
        raw.get("LONGTERMDEBTPRIORFISCALYEAR"),
    )

    # Classify instrument type from SECURITYOFFEREDTYPE
    sec_type = result["security_type"].lower()
    if "debt" in sec_type or "note" in sec_type:
        result["instrument_type"] = "convertible_note"
    elif "safe" in sec_type:
        result["instrument_type"] = "safe"
    else:
        result["instrument_type"] = "equity"

    # Submission type
    result["submission_type"] = (raw.get("SUBMISSION_TYPE", "") or "").strip()

    # Platform (intermediary)
    result["platform_name"] = (raw.get("COMPANYNAME", "") or "").strip() or None

    return result


# ---------------------------------------------------------------------------
# Resumability check
# ---------------------------------------------------------------------------


def _is_quarter_ingested_cf(
    conn: psycopg.Connection, year: int, quarter: int,
) -> bool:
    """Check if a given quarter has already been ingested for DERA CF."""
    from startuplens.db import execute_query

    rows = execute_query(
        conn,
        "SELECT 1 FROM companies WHERE source = 'sec_dera_cf' "
        "AND source_id LIKE %s LIMIT 1",
        (f"%_q{year}Q{quarter}",),
    )
    return len(rows) > 0


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------


def ingest_dera_cf_batch(conn: psycopg.Connection, records: list[dict]) -> int:
    """Insert normalized DERA CF records into companies, funding_rounds,
    crowdfunding_outcomes, and financial_data tables.

    Returns number of companies inserted/updated.
    """
    if not records:
        return 0

    chunk_size = 500
    inserted = 0

    for chunk_start in range(0, len(records), chunk_size):
        chunk = records[chunk_start: chunk_start + chunk_size]

        # Deduplicate by source_id within chunk (keep first)
        seen: dict[str, dict] = {}
        for rec in chunk:
            sid = rec.get("source_id", "")
            if sid and sid not in seen:
                seen[sid] = rec
        deduped = list(seen.values())

        if not deduped:
            continue

        # Phase 1: Upsert companies
        co_placeholders = ", ".join(
            ["(%s, %s, %s, %s, %s, %s, %s)"] * len(deduped),
        )
        co_values = []
        for rec in deduped:
            co_values.extend([
                rec.get("name"),
                rec.get("country", "US"),
                None,  # sector
                None,  # sic_code
                rec.get("date_incorporation"),
                "sec_dera_cf",
                rec.get("source_id"),
            ])

        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO companies (name, country, sector, sic_code,
                    founding_date, source, source_id)
                VALUES {co_placeholders}
                ON CONFLICT (source, source_id) WHERE source_id IS NOT NULL
                DO UPDATE SET
                    name = EXCLUDED.name,
                    founding_date = COALESCE(EXCLUDED.founding_date, companies.founding_date)
                RETURNING id, source_id
                """,
                co_values,
            )
            returned = cur.fetchall()

        id_by_source = {row["source_id"]: row["id"] for row in returned}

        # Phase 2: Insert funding_rounds
        fr_rows = []
        for rec in deduped:
            company_id = id_by_source.get(rec.get("source_id"))
            if not company_id:
                continue
            amount = rec.get("offering_amount")
            if not amount:
                continue
            fr_rows.append((
                company_id,
                rec.get("filing_date"),
                "reg_cf",
                rec.get("instrument_type", "equity"),
                amount,
                None,  # pre_money_valuation
                rec.get("platform_name"),
                "sec_dera_cf",
            ))

        if fr_rows:
            fr_ph = ", ".join(["(%s, %s, %s, %s, %s, %s, %s, %s)"] * len(fr_rows))
            fr_flat = [v for row in fr_rows for v in row]
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO funding_rounds (
                        company_id, round_date, round_type, instrument_type,
                        amount_raised, pre_money_valuation, platform, source
                    ) VALUES {fr_ph}
                    """,
                    fr_flat,
                )

        # Phase 3: Upsert crowdfunding_outcomes
        co_rows = []
        for rec in deduped:
            company_id = id_by_source.get(rec.get("source_id"))
            if not company_id:
                continue
            revenue = rec.get("revenue_recent")
            has_revenue = revenue is not None and revenue > 0
            co_rows.append((
                company_id,
                rec.get("platform_name"),
                rec.get("filing_date"),
                rec.get("offering_amount"),
                rec.get("max_offering_amount"),
                has_revenue,
                revenue,
                "seed",
                "unknown",
                3,  # label_quality_tier
                "sec_dera_cf",
            ))

        if co_rows:
            co_ph = ", ".join(
                ["(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"] * len(co_rows),
            )
            co_flat = [v for row in co_rows for v in row]
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO crowdfunding_outcomes (
                        company_id, platform, campaign_date, funding_target,
                        amount_raised, had_revenue, revenue_at_raise,
                        stage_bucket, outcome, label_quality_tier, data_source
                    ) VALUES {co_ph}
                    ON CONFLICT DO NOTHING
                    """,
                    co_flat,
                )

        # Phase 4: Insert financial_data (most recent fiscal year)
        fd_rows = []
        for rec in deduped:
            company_id = id_by_source.get(rec.get("source_id"))
            if not company_id:
                continue
            # Only insert if we have at least one financial field
            if not any(rec.get(f) is not None for f in (
                "revenue_recent", "total_assets_recent", "cash_recent",
                "net_income_recent",
            )):
                continue
            total_debt = None
            st = rec.get("short_term_debt_recent")
            lt = rec.get("long_term_debt_recent")
            if st is not None or lt is not None:
                total_debt = (st or 0) + (lt or 0)

            # Use filing_date as proxy for period_end_date
            period_date = rec.get("filing_date")
            if not period_date:
                continue
            fd_rows.append((
                company_id,
                period_date,
                "annual",
                rec.get("revenue_recent"),
                None,  # revenue_growth_yoy
                None,  # gross_profit
                None,  # gross_margin
                None,  # operating_income
                rec.get("net_income_recent"),
                rec.get("cash_recent"),
                rec.get("total_assets_recent"),
                None,  # total_liabilities
                total_debt,
                rec.get("employees"),
                None,  # burn_rate_monthly
                None,  # customers
                "sec_dera_cf",
            ))

        if fd_rows:
            fd_ph = ", ".join(
                ["(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"]
                * len(fd_rows),
            )
            fd_flat = [v for row in fd_rows for v in row]
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO financial_data (
                        company_id, period_end_date, period_type,
                        revenue, revenue_growth_yoy, gross_profit,
                        gross_margin, operating_income, net_income,
                        cash_and_equivalents, total_assets, total_liabilities,
                        total_debt, employee_count, burn_rate_monthly,
                        customers, source_filing
                    ) VALUES {fd_ph}
                    """,
                    fd_flat,
                )

        inserted += len(returned)
        conn.commit()

    logger.info("ingested_dera_cf_batch", inserted=inserted)
    return inserted


# ---------------------------------------------------------------------------
# Pipeline orchestrator
# ---------------------------------------------------------------------------


def _process_quarter(
    settings: Settings,
    year: int,
    quarter: int,
    output_dir: Path,
) -> dict[str, Any]:
    """Process a single quarter: download, parse, normalize, ingest."""
    from startuplens.db import get_connection

    quarter_key = f"{year}-Q{quarter}"
    conn = get_connection(settings)

    try:
        if _is_quarter_ingested_cf(conn, year, quarter):
            logger.info("skipping_ingested_quarter_cf", quarter=quarter_key)
            return {"status": "skipped", "quarter": quarter_key}

        zip_path = download_dera_cf_dataset(
            year, quarter, output_dir, settings=settings,
        )

        raw_records = parse_dera_cf_dataset(zip_path)
        normalized = [normalize_dera_cf_record(r) for r in raw_records]

        # Add quarter suffix for resumability
        for rec in normalized:
            cik = rec.get("cik", "0")
            rec["source_id"] = f"{cik}_q{year}Q{quarter}"

        count = ingest_dera_cf_batch(conn, normalized)

        logger.info(
            "quarter_complete_cf",
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
        logger.warning("quarter_http_error_cf", quarter=quarter_key, error=error_msg)
        return {"status": "error", "quarter": quarter_key, "error": error_msg}
    except Exception as e:
        error_msg = f"{quarter_key}: {e!s}"
        logger.warning("quarter_error_cf", quarter=quarter_key, error=error_msg)
        return {"status": "error", "quarter": quarter_key, "error": error_msg}
    finally:
        conn.close()


def run_dera_cf_pipeline(
    conn: psycopg.Connection,
    settings: Settings,
    years: list[int],
    *,
    output_dir: Path | None = None,
    max_workers: int = 4,
) -> dict[str, Any]:
    """Orchestrate the full SEC DERA CF pipeline with concurrent quarters.

    Downloads quarterly ZIP datasets, parses filings, normalizes records,
    and ingests into the database. Skips quarters already ingested.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    if output_dir is None:
        output_dir = Path("data/sec_dera_cf")

    summary: dict[str, Any] = {
        "years": years,
        "quarters_processed": 0,
        "quarters_skipped": 0,
        "filings_parsed": 0,
        "records_ingested": 0,
        "errors": [],
    }

    quarters = [(y, q) for y in years for q in (1, 2, 3, 4)]

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _process_quarter, settings, year, quarter, output_dir,
            ): (year, quarter)
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
        "dera_cf_pipeline_complete",
        quarters_processed=summary["quarters_processed"],
        records_ingested=summary["records_ingested"],
        errors=len(summary["errors"]),
    )
    return summary
