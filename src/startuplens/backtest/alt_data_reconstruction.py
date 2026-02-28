"""Historical alternative data reconstruction for backtesting.

Reconstructs 8 alternative data signals that can be used as features
in backtesting without introducing look-ahead bias. Each signal is
reconstructed from sources that were available at the time of the
original campaign.

Signals:
  1. Wayback Machine — website activity snapshots
  2. Companies House filing recency
  3. Job posting volume (via archived data)
  4. App store ratings (where applicable)
  5. Social media follower counts
  6. Patent/trademark filings
  7. News mention sentiment
  8. Regulatory compliance flags
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class AltDataSignal:
    """A single reconstructed alternative data signal."""

    signal_name: str
    entity_id: str
    as_of_date: date
    value: Any
    source: str
    confidence: float  # 0.0 to 1.0


def reconstruct_wayback_signal(
    entity_id: str,
    domain: str,
    as_of_date: date,
) -> AltDataSignal | None:
    """Check Wayback Machine for website activity around a given date.

    Returns a signal indicating whether the company's website was active
    (captured) close to the as_of_date.
    """
    # Wayback CDX API: http://web.archive.org/cdx/search/cdx
    # Query for captures within ±6 months of as_of_date
    # This is a placeholder — actual implementation calls the CDX API
    return AltDataSignal(
        signal_name="wayback_active",
        entity_id=entity_id,
        as_of_date=as_of_date,
        value=None,  # True/False once implemented
        source="wayback_machine",
        confidence=0.0,
    )


def reconstruct_filing_recency_signal(
    entity_id: str,
    company_number: str,
    as_of_date: date,
    filing_history: list[dict] | None = None,
) -> AltDataSignal | None:
    """Compute filing recency from Companies House history.

    Uses the filing_history (if provided) or fetches it. Returns the number
    of days since the most recent filing before as_of_date.
    """
    if not filing_history:
        return AltDataSignal(
            signal_name="filing_recency_days",
            entity_id=entity_id,
            as_of_date=as_of_date,
            value=None,
            source="companies_house",
            confidence=0.0,
        )

    filings_before = [
        f for f in filing_history
        if f.get("date") and date.fromisoformat(f["date"]) <= as_of_date
    ]

    if not filings_before:
        return AltDataSignal(
            signal_name="filing_recency_days",
            entity_id=entity_id,
            as_of_date=as_of_date,
            value=None,
            source="companies_house",
            confidence=0.5,
        )

    most_recent = max(filings_before, key=lambda f: f["date"])
    days_since = (as_of_date - date.fromisoformat(most_recent["date"])).days

    return AltDataSignal(
        signal_name="filing_recency_days",
        entity_id=entity_id,
        as_of_date=as_of_date,
        value=days_since,
        source="companies_house",
        confidence=0.9,
    )


def reconstruct_job_posting_signal(
    entity_id: str,
    company_name: str,
    as_of_date: date,
) -> AltDataSignal | None:
    """Reconstruct historical job posting volume.

    Uses archived job board data to estimate hiring activity around as_of_date.
    """
    return AltDataSignal(
        signal_name="job_posting_volume",
        entity_id=entity_id,
        as_of_date=as_of_date,
        value=None,
        source="archived_job_boards",
        confidence=0.0,
    )


def reconstruct_app_store_signal(
    entity_id: str,
    app_id: str | None,
    as_of_date: date,
) -> AltDataSignal | None:
    """Reconstruct historical app store ratings if applicable."""
    if not app_id:
        return None

    return AltDataSignal(
        signal_name="app_store_rating",
        entity_id=entity_id,
        as_of_date=as_of_date,
        value=None,
        source="app_store",
        confidence=0.0,
    )


def reconstruct_social_media_signal(
    entity_id: str,
    handles: dict[str, str] | None,
    as_of_date: date,
) -> AltDataSignal | None:
    """Reconstruct historical social media follower counts."""
    if not handles:
        return None

    return AltDataSignal(
        signal_name="social_followers",
        entity_id=entity_id,
        as_of_date=as_of_date,
        value=None,
        source="social_media_archive",
        confidence=0.0,
    )


def reconstruct_patent_signal(
    entity_id: str,
    company_name: str,
    as_of_date: date,
) -> AltDataSignal | None:
    """Check patent/trademark filings before as_of_date."""
    return AltDataSignal(
        signal_name="patent_count",
        entity_id=entity_id,
        as_of_date=as_of_date,
        value=None,
        source="patent_office",
        confidence=0.0,
    )


def reconstruct_news_sentiment_signal(
    entity_id: str,
    company_name: str,
    as_of_date: date,
) -> AltDataSignal | None:
    """Reconstruct news mention sentiment around as_of_date."""
    return AltDataSignal(
        signal_name="news_sentiment",
        entity_id=entity_id,
        as_of_date=as_of_date,
        value=None,
        source="news_archive",
        confidence=0.0,
    )


def reconstruct_regulatory_signal(
    entity_id: str,
    company_number: str | None,
    as_of_date: date,
) -> AltDataSignal | None:
    """Check for regulatory compliance flags before as_of_date."""
    if not company_number:
        return None

    return AltDataSignal(
        signal_name="regulatory_flags",
        entity_id=entity_id,
        as_of_date=as_of_date,
        value=None,
        source="regulatory_records",
        confidence=0.0,
    )


ALL_SIGNALS = [
    ("wayback_active", reconstruct_wayback_signal),
    ("filing_recency_days", reconstruct_filing_recency_signal),
    ("job_posting_volume", reconstruct_job_posting_signal),
    ("app_store_rating", reconstruct_app_store_signal),
    ("social_followers", reconstruct_social_media_signal),
    ("patent_count", reconstruct_patent_signal),
    ("news_sentiment", reconstruct_news_sentiment_signal),
    ("regulatory_flags", reconstruct_regulatory_signal),
]


def reconstruct_all_signals(
    entity_id: str,
    as_of_date: date,
    company_data: dict,
) -> list[AltDataSignal]:
    """Reconstruct all 8 alternative data signals for one entity.

    Parameters
    ----------
    entity_id:
        The canonical entity UUID.
    as_of_date:
        The date for temporal correctness.
    company_data:
        Dict with keys: domain, company_number, company_name, app_id,
        social_handles, filing_history.

    Returns
    -------
    list[AltDataSignal]
        All successfully reconstructed signals (excludes None results).
    """
    signals: list[AltDataSignal] = []

    wayback = reconstruct_wayback_signal(
        entity_id, company_data.get("domain", ""), as_of_date
    )
    if wayback:
        signals.append(wayback)

    filing = reconstruct_filing_recency_signal(
        entity_id,
        company_data.get("company_number", ""),
        as_of_date,
        filing_history=company_data.get("filing_history"),
    )
    if filing:
        signals.append(filing)

    jobs = reconstruct_job_posting_signal(
        entity_id, company_data.get("company_name", ""), as_of_date
    )
    if jobs:
        signals.append(jobs)

    app = reconstruct_app_store_signal(
        entity_id, company_data.get("app_id"), as_of_date
    )
    if app:
        signals.append(app)

    social = reconstruct_social_media_signal(
        entity_id, company_data.get("social_handles"), as_of_date
    )
    if social:
        signals.append(social)

    patent = reconstruct_patent_signal(
        entity_id, company_data.get("company_name", ""), as_of_date
    )
    if patent:
        signals.append(patent)

    news = reconstruct_news_sentiment_signal(
        entity_id, company_data.get("company_name", ""), as_of_date
    )
    if news:
        signals.append(news)

    regulatory = reconstruct_regulatory_signal(
        entity_id, company_data.get("company_number"), as_of_date
    )
    if regulatory:
        signals.append(regulatory)

    logger.info(
        "alt_data_reconstruction_complete",
        entity_id=entity_id,
        as_of_date=str(as_of_date),
        signals_found=len(signals),
    )
    return signals
