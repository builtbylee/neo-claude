"""SEC EDGAR Form C narrative text extraction.

Downloads Form C filing documents from EDGAR, extracts narrative text
from exhibit documents (OFFERING MEMORANDUM, EXECUTIVE SUMMARY, etc.)
and structured data from the primary XML, stores in sec_form_c_texts.

Rate limit: SEC requests 10 req/s max with descriptive User-Agent header.
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Any

import httpx
import structlog
from bs4 import BeautifulSoup

if TYPE_CHECKING:
    import psycopg

    from startuplens.config import Settings

logger = structlog.get_logger(__name__)

# SEC rate limit: 10 requests/s
_MIN_REQUEST_INTERVAL = 0.12  # slightly conservative

# Retry settings
_MAX_RETRIES = 3
_RETRY_BACKOFF_BASE = 5.0  # seconds

# Narrative exhibit keywords (uppercase match against filing document descriptions)
_NARRATIVE_KEYWORDS = (
    "OFFERING MEMORANDUM",
    "OFFERING CIRCULAR",
    "OFFERING STATEMENT",
    "BUSINESS PLAN",
    "EXECUTIVE SUMMARY",
    "FORM C",
    "DISCLOSURE",
)

# Form C XML namespace
_FC_NS = "http://www.sec.gov/edgar/formc"

# Minimum useful text length
_MIN_TEXT_LENGTH = 100


class _RateLimiter:
    """Thread-safe rate limiter for SEC requests."""

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
    return httpx.Client(
        headers={
            "User-Agent": settings.sec_user_agent,
            "Accept-Encoding": "gzip, deflate",
        },
        follow_redirects=True,
        timeout=120.0,
    )


def _fetch_with_retry(client: httpx.Client, url: str) -> httpx.Response | None:
    """Fetch a URL with rate limiting and exponential backoff retry."""
    for attempt in range(_MAX_RETRIES):
        _rate_limiter.wait()
        try:
            resp = client.get(url)
            if resp.status_code == 200:
                return resp
            if resp.status_code in (429, 503):
                wait = _RETRY_BACKOFF_BASE * (2**attempt)
                logger.warning("sec_rate_limited", url=url, status=resp.status_code, wait=wait)
                time.sleep(wait)
                continue
            logger.warning("sec_fetch_failed", url=url, status=resp.status_code)
            return None
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            wait = _RETRY_BACKOFF_BASE * (2**attempt)
            logger.warning("sec_fetch_error", url=url, error=str(exc), wait=wait)
            time.sleep(wait)
    logger.error("sec_fetch_exhausted", url=url, retries=_MAX_RETRIES)
    return None


# ---------------------------------------------------------------------------
# Full submission text parsing (SGML)
# ---------------------------------------------------------------------------


def _parse_submission_text(text: str) -> list[dict[str, str]]:
    """Parse an EDGAR full submission text file into individual documents.

    The submission text file contains all documents in SGML format:
    <DOCUMENT>
    <TYPE>C
    <SEQUENCE>1
    <FILENAME>primary_doc.xml
    <DESCRIPTION>...
    <TEXT>
    ... document content ...
    </TEXT>
    </DOCUMENT>
    """
    documents: list[dict[str, str]] = []
    doc_pattern = re.compile(
        r"<DOCUMENT>\s*"
        r"<TYPE>([^\n]*)\n"
        r"(?:<SEQUENCE>([^\n]*)\n)?"
        r"(?:<FILENAME>([^\n]*)\n)?"
        r"(?:<DESCRIPTION>([^\n]*)\n)?"
        r"<TEXT>\s*(.*?)\s*</TEXT>",
        re.DOTALL,
    )

    for match in doc_pattern.finditer(text):
        documents.append({
            "type": match.group(1).strip(),
            "sequence": (match.group(2) or "").strip(),
            "filename": (match.group(3) or "").strip(),
            "description": (match.group(4) or "").strip(),
            "content": match.group(5).strip(),
        })

    return documents


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------


def extract_narrative_from_html(html: str) -> str:
    """Extract readable text from HTML, removing tags, scripts, and styles."""
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "meta", "link", "head"]):
        tag.decompose()

    text = soup.get_text(separator="\n", strip=True)

    # Clean up excessive whitespace
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)


def extract_narrative_from_xml(xml_text: str) -> dict[str, str]:
    """Extract narrative fields from Form C primary XML."""
    sections: dict[str, str] = {}

    try:
        # Strip XML declaration if present for parsing
        clean = re.sub(r"<\?xml[^?]*\?>", "", xml_text).strip()
        root = ET.fromstring(clean)
    except ET.ParseError:
        return sections

    ns = {"fc": _FC_NS}

    # Extract all text content from key sections
    def _get_text(path: str) -> str | None:
        elem = root.find(path, ns)
        if elem is not None and elem.text:
            return elem.text.strip()
        return None

    # Company identity
    name = _get_text(".//fc:nameOfIssuer")
    if name:
        sections["company_name"] = name

    # Legal form and jurisdiction
    legal_form = _get_text(".//fc:legalStatusForm")
    jurisdiction = _get_text(".//fc:jurisdictionOrganization")
    if legal_form:
        sections["legal_status"] = f"{legal_form} ({jurisdiction or 'unknown'})"

    # Offering details
    sec_type = _get_text(".//fc:securityOfferedType")
    sec_desc = _get_text(".//fc:securityOfferedOtherDesc")
    if sec_type or sec_desc:
        sections["security_type"] = sec_desc or sec_type or ""

    comp = _get_text(".//fc:compensationAmount")
    if comp:
        sections["compensation"] = comp

    desc_oversub = _get_text(".//fc:descOverSubscription")
    if desc_oversub:
        sections["oversubscription_policy"] = desc_oversub

    price_method = _get_text(".//fc:priceDeterminationMethod")
    if price_method and price_method.upper() != "N/A":
        sections["price_method"] = price_method

    return sections


def _build_structured_narrative(
    xml_sections: dict[str, str],
    company_name: str,
    offering_amount: float | None = None,
    max_offering: float | None = None,
    revenue: float | None = None,
    employees: int | None = None,
    net_income: float | None = None,
) -> str:
    """Build a narrative from structured data when no exhibit text is available."""
    parts = []
    name = xml_sections.get("company_name", company_name)
    parts.append(f"Company: {name}")

    if xml_sections.get("legal_status"):
        parts.append(f"Legal structure: {xml_sections['legal_status']}")

    if xml_sections.get("security_type"):
        parts.append(f"Security offered: {xml_sections['security_type']}")

    if offering_amount:
        parts.append(f"Offering amount: ${offering_amount:,.0f}")
    if max_offering:
        parts.append(f"Maximum offering: ${max_offering:,.0f}")

    if employees is not None:
        parts.append(f"Employees: {employees}")
    if revenue is not None:
        parts.append(f"Revenue (most recent FY): ${revenue:,.0f}")
    if net_income is not None:
        parts.append(f"Net income (most recent FY): ${net_income:,.0f}")

    if xml_sections.get("compensation"):
        parts.append(f"Intermediary compensation: {xml_sections['compensation']}")
    if xml_sections.get("oversubscription_policy"):
        parts.append(f"Oversubscription: {xml_sections['oversubscription_policy']}")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Filing text extraction pipeline
# ---------------------------------------------------------------------------


def _filer_cik_from_accession(accession_number: str) -> str:
    """Extract the filer CIK from an accession number.

    EDGAR stores filings under the FILER's CIK (first 10 digits of the
    accession number), not necessarily the issuer's CIK. For Form C filings
    through intermediaries like StartEngine, the filer is the intermediary.
    """
    # Accession format: XXXXXXXXXX-YY-ZZZZZZ (filer CIK is first 10 digits)
    filer_cik = accession_number.replace("-", "")[:10].lstrip("0") or "0"
    return filer_cik


def fetch_filing_text(
    client: httpx.Client,
    cik: str,
    accession_number: str,
) -> str | None:
    """Fetch and extract narrative text from a Form C filing on EDGAR.

    Strategy:
    1. Try the full submission text file (contains all documents)
    2. Parse SGML to find exhibit documents with narrative content
    3. Fall back to primary XML structured data if no exhibits found

    Returns concatenated narrative text or None if nothing useful found.
    """
    acc_nd = accession_number.replace("-", "")
    # EDGAR paths use the filer's CIK, not necessarily the issuer's
    path_cik = _filer_cik_from_accession(accession_number)

    # Step 1: Try fetching the full submission text
    submission_url = (
        f"https://www.sec.gov/Archives/edgar/data/{path_cik}/{acc_nd}/{acc_nd}.txt"
    )
    resp = _fetch_with_retry(client, submission_url)

    if resp is not None:
        return _extract_from_submission(resp.text, cik, accession_number)

    # Step 2: Fall back to just the primary XML
    primary_url = (
        f"https://www.sec.gov/Archives/edgar/data/{path_cik}/{acc_nd}/primary_doc.xml"
    )
    resp = _fetch_with_retry(client, primary_url)
    if resp is not None:
        xml_sections = extract_narrative_from_xml(resp.text)
        if xml_sections:
            return _build_structured_narrative(xml_sections, company_name=cik)

    return None


def _extract_from_submission(submission_text: str, cik: str, accession: str) -> str | None:
    """Extract narrative text from a parsed EDGAR submission file."""
    documents = _parse_submission_text(submission_text)
    if not documents:
        logger.warning("no_documents_parsed", cik=cik, accession=accession)
        return None

    narrative_parts: list[str] = []
    source_sections: list[str] = []

    # First pass: look for narrative exhibits
    for doc in documents:
        desc = doc["description"].upper()
        doc_type = doc["type"].upper()

        # Skip non-narrative documents
        if doc_type in ("GRAPHIC", "ZIP", "EXCEL", "XBRL"):
            continue

        is_narrative = any(kw in desc for kw in _NARRATIVE_KEYWORDS)

        if is_narrative and doc["content"]:
            content = doc["content"]

            # Detect HTML vs plain text
            if "<html" in content.lower() or "<body" in content.lower() or "<div" in content.lower():
                text = extract_narrative_from_html(content)
            else:
                # Plain text or XML — strip XML tags if present
                text = re.sub(r"<[^>]+>", " ", content)
                text = re.sub(r"\s+", " ", text).strip()

            if len(text) >= _MIN_TEXT_LENGTH:
                narrative_parts.append(text)
                source_sections.append(doc["description"] or doc["type"])

    # If we found narrative exhibits, concatenate them
    if narrative_parts:
        return "\n\n---\n\n".join(narrative_parts)

    # Second pass: extract structured data from primary XML
    primary = next(
        (d for d in documents if d["type"].upper().startswith("C") and d["content"]),
        None,
    )
    if primary:
        xml_sections = extract_narrative_from_xml(primary["content"])
        if xml_sections:
            narrative = _build_structured_narrative(xml_sections, company_name=cik)
            if len(narrative) >= _MIN_TEXT_LENGTH:
                return narrative

    return None


# ---------------------------------------------------------------------------
# Batch scraping pipeline
# ---------------------------------------------------------------------------


def _get_companies_to_scrape(
    conn: psycopg.Connection,
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Get companies with outcomes that need text scraping.

    Returns companies from sec_dera_cf/sec_edgar that:
    - Have clear outcomes (trading/exited/failed) in crowdfunding_outcomes
    - Have linked filings in sec_cf_filings
    - Don't already have text in sec_form_c_texts
    """
    from startuplens.db import execute_query

    query = """
        SELECT DISTINCT ON (c.id)
            c.id AS company_id,
            c.name AS company_name,
            SPLIT_PART(c.source_id, '_q', 1) AS cik,
            f.accession_number,
            f.filing_date
        FROM companies c
        JOIN crowdfunding_outcomes co ON co.company_id = c.id
        JOIN sec_cf_filings f ON f.cik = SPLIT_PART(c.source_id, '_q', 1)
        LEFT JOIN sec_form_c_texts t ON t.company_id = c.id
        WHERE c.source IN ('sec_dera_cf', 'sec_edgar')
          AND co.outcome IN ('trading', 'exited', 'failed')
          AND t.id IS NULL
          AND f.submission_type IN ('C', 'C-U', 'C/A')
        ORDER BY c.id, f.filing_date ASC
    """
    if limit:
        query += f"\n        LIMIT {int(limit)}"

    return execute_query(conn, query)


def scrape_form_c_texts(
    conn: psycopg.Connection,
    settings: Settings,
    *,
    limit: int | None = None,
) -> int:
    """Scrape Form C narrative text for companies with outcomes.

    Returns the number of texts successfully scraped and stored.
    """
    companies = _get_companies_to_scrape(conn, limit=limit)
    logger.info("scrape_targets", count=len(companies))

    if not companies:
        return 0

    client = _build_client(settings)
    scraped = 0

    try:
        for i, row in enumerate(companies):
            company_id = row["company_id"]
            cik = row["cik"]
            accession = row["accession_number"]
            company_name = row["company_name"]
            filing_date = row["filing_date"]

            logger.info(
                "scraping_filing",
                company=company_name,
                cik=cik,
                accession=accession,
                progress=f"{i + 1}/{len(companies)}",
            )

            text = fetch_filing_text(client, cik, accession)

            if text and len(text) >= _MIN_TEXT_LENGTH:
                _store_text(conn, company_id, cik, accession, filing_date, text)
                scraped += 1
                logger.info("text_stored", company=company_name, length=len(text))
            else:
                logger.info("text_too_short", company=company_name, cik=cik)
    finally:
        client.close()

    logger.info("scrape_complete", scraped=scraped, total=len(companies))
    return scraped


def _store_text(
    conn: psycopg.Connection,
    company_id: str,
    cik: str,
    accession_number: str,
    filing_date: Any,
    narrative_text: str,
) -> None:
    """Insert scraped text into sec_form_c_texts table."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sec_form_c_texts
                (company_id, cik, accession_number, filing_date, narrative_text, text_length)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (company_id, accession_number) DO NOTHING
            """,
            (company_id, cik, accession_number, filing_date, narrative_text, len(narrative_text)),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def run_text_scraper(*, limit: int | None = None) -> int:
    """Run the text scraper pipeline. Returns count of texts scraped."""
    from startuplens.config import get_settings
    from startuplens.db import get_connection

    settings = get_settings()
    conn = get_connection(settings)
    try:
        return scrape_form_c_texts(conn, settings, limit=limit)
    finally:
        conn.close()
