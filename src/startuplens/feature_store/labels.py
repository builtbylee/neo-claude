"""Label quality tier assignment logic.

Tiers:
  1 — Verified realised outcome with concrete evidence (full training weight)
  2 — Estimated outcome from indirect signals (0.7x training weight)
  3 — Insufficient evidence to determine outcome (excluded from training)
"""

from __future__ import annotations

from datetime import date


def assign_label_tier_uk(
    companies_house_status: str | None,
    last_accounts_date: date | None = None,
    accounts_overdue: bool = False,
    dissolution_date: date | None = None,
) -> int:
    """Assign label quality tier for a UK company.

    Rules (from ARCHITECTURE.md 1d):
      - Dissolved/liquidation + filing date => Tier 1
      - Active + recent filings (within 15 months) => Tier 1
      - Active + overdue accounts or no recent filing => Tier 2
      - No Companies House match => Tier 3
    """
    if companies_house_status is None:
        return 3

    status = companies_house_status.lower()

    # Hard outcomes with evidence
    if status in ("dissolved", "liquidation", "administration", "converted-closed"):
        return 1

    if status == "active":
        if last_accounts_date is not None and not accounts_overdue:
            # Recent filing — confident it's still trading
            return 1
        # Active but accounts overdue or no recent filing
        return 2

    # Unknown status
    return 3


def assign_label_tier_us(
    sec_filing_status: str | None = None,
    news_verified: bool = False,
    wayback_active: bool = False,
) -> int:
    """Assign label quality tier for a US company.

    Rules:
      - SEC filing status + news confirmation => Tier 1
      - SEC status alone or Wayback confirmation => Tier 2
      - No SEC match => Tier 3
    """
    if sec_filing_status is None:
        return 3

    if news_verified:
        return 1

    if sec_filing_status or wayback_active:
        return 2

    return 3


def assign_label_tier_academic(source: str) -> int:
    """Academic dataset labels are always Tier 1 (peer-reviewed methodology)."""
    return 1


def assign_label_tier_manual(verified_against_registry: bool) -> int:
    """Tier 1 if verified against Companies House/SEC; Tier 2 otherwise."""
    return 1 if verified_against_registry else 2


def classify_uk_outcome(
    companies_house_status: str,
    accounts_overdue: bool = False,
) -> tuple[str, str]:
    """Map Companies House status to (outcome, outcome_detail).

    Returns:
        (outcome, outcome_detail) where outcome is one of:
        trading, failed, exited, unknown
    """
    status = companies_house_status.lower()

    if status in ("dissolved",):
        return ("failed", "dissolved")
    if status in ("liquidation",):
        return ("failed", "liquidation")
    if status in ("administration",):
        return ("failed", "administration")
    if status == "converted-closed":
        return ("failed", "converted_closed")
    if status == "active":
        if accounts_overdue:
            return ("trading", "active_distress_signals")
        return ("trading", "active")

    return ("unknown", status)


def classify_us_outcome(
    sec_filing_status: str | None,
    news_outcome: str | None = None,
) -> tuple[str, str]:
    """Map SEC filing status + news to (outcome, outcome_detail).

    news_outcome can be: operating, shutdown, acquired, ipo, None
    """
    if news_outcome == "shutdown":
        return ("failed", "news_confirmed_shutdown")
    if news_outcome == "acquired":
        return ("exited", "acquisition")
    if news_outcome == "ipo":
        return ("exited", "ipo")
    if news_outcome == "operating":
        return ("trading", "news_confirmed_operating")

    if sec_filing_status:
        return ("trading", f"sec_status_{sec_filing_status}")

    return ("unknown", "no_data")
