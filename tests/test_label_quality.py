"""Tests for label quality tier assignment."""

from datetime import date

from startuplens.feature_store.labels import (
    assign_label_tier_academic,
    assign_label_tier_manual,
    assign_label_tier_uk,
    assign_label_tier_us,
    classify_uk_outcome,
    classify_us_outcome,
)


class TestUKLabelTier:
    def test_dissolved_is_tier1(self):
        assert assign_label_tier_uk("dissolved") == 1

    def test_liquidation_is_tier1(self):
        assert assign_label_tier_uk("liquidation") == 1

    def test_administration_is_tier1(self):
        assert assign_label_tier_uk("administration") == 1

    def test_active_with_recent_filings_is_tier1(self):
        assert assign_label_tier_uk(
            "active",
            last_accounts_date=date(2025, 6, 1),
            accounts_overdue=False,
        ) == 1

    def test_active_with_overdue_accounts_is_tier2(self):
        assert assign_label_tier_uk(
            "active",
            last_accounts_date=date(2023, 1, 1),
            accounts_overdue=True,
        ) == 2

    def test_active_no_filing_info_is_tier2(self):
        assert assign_label_tier_uk("active") == 2

    def test_no_match_is_tier3(self):
        assert assign_label_tier_uk(None) == 3

    def test_case_insensitive(self):
        assert assign_label_tier_uk("Dissolved") == 1
        assert assign_label_tier_uk("ACTIVE") == 2


class TestUSLabelTier:
    def test_sec_plus_news_is_tier1(self):
        assert assign_label_tier_us(sec_filing_status="current", news_verified=True) == 1

    def test_sec_only_is_tier2(self):
        assert assign_label_tier_us(sec_filing_status="current", news_verified=False) == 2

    def test_wayback_only_is_tier2(self):
        assert assign_label_tier_us(sec_filing_status="unknown", wayback_active=True) == 2

    def test_no_data_is_tier3(self):
        assert assign_label_tier_us(sec_filing_status=None) == 3


class TestAcademicAndManualTiers:
    def test_academic_always_tier1(self):
        assert assign_label_tier_academic("walthoff_borm") == 1
        assert assign_label_tier_academic("signori_vismara") == 1

    def test_manual_verified_is_tier1(self):
        assert assign_label_tier_manual(verified_against_registry=True) == 1

    def test_manual_unverified_is_tier2(self):
        assert assign_label_tier_manual(verified_against_registry=False) == 2


class TestUKOutcomeClassification:
    def test_dissolved(self):
        assert classify_uk_outcome("dissolved") == ("failed", "dissolved")

    def test_liquidation(self):
        assert classify_uk_outcome("liquidation") == ("failed", "liquidation")

    def test_active_healthy(self):
        assert classify_uk_outcome("active", accounts_overdue=False) == ("trading", "active")

    def test_active_distress(self):
        outcome, detail = classify_uk_outcome("active", accounts_overdue=True)
        assert outcome == "trading"
        assert "distress" in detail


class TestUSOutcomeClassification:
    def test_news_shutdown(self):
        assert classify_us_outcome(None, news_outcome="shutdown") == (
            "failed", "news_confirmed_shutdown"
        )

    def test_news_acquired(self):
        assert classify_us_outcome(None, news_outcome="acquired") == ("exited", "acquisition")

    def test_sec_status_only(self):
        outcome, _ = classify_us_outcome(sec_filing_status="current")
        assert outcome == "trading"

    def test_no_data(self):
        assert classify_us_outcome(None) == ("unknown", "no_data")
