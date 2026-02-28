"""Tests for baseline scoring strategies."""

from __future__ import annotations

import pytest

from startuplens.backtest.baselines import (
    ScoredDeal,
    heuristic_baseline,
    random_baseline,
    sector_momentum_baseline,
)


# ------------------------------------------------------------------
# Random baseline
# ------------------------------------------------------------------


class TestRandomBaseline:
    """Verify deterministic random scoring."""

    def test_scores_are_deterministic_with_same_seed(self, sample_deals):
        scored_a = random_baseline(sample_deals, seed=42)
        scored_b = random_baseline(sample_deals, seed=42)
        assert [d.score for d in scored_a] == [d.score for d in scored_b]

    def test_different_seed_gives_different_scores(self, sample_deals):
        scored_a = random_baseline(sample_deals, seed=42)
        scored_b = random_baseline(sample_deals, seed=99)
        # Extremely unlikely to be identical
        assert [d.score for d in scored_a] != [d.score for d in scored_b]

    def test_scores_in_range(self, sample_deals):
        scored = random_baseline(sample_deals)
        for d in scored:
            assert 0 <= d.score <= 100

    def test_preserves_entity_ids(self, sample_deals):
        scored = random_baseline(sample_deals)
        assert [d.entity_id for d in scored] == [d.entity_id for d in sample_deals]

    def test_preserves_metadata(self, sample_deals):
        scored = random_baseline(sample_deals)
        for orig, s in zip(sample_deals, scored):
            assert s.sector == orig.sector
            assert s.platform == orig.platform
            assert s.campaign_date == orig.campaign_date
            assert s.outcome == orig.outcome

    def test_empty_input(self):
        assert random_baseline([]) == []


# ------------------------------------------------------------------
# Heuristic baseline
# ------------------------------------------------------------------


class TestHeuristicBaseline:
    """Verify the has_revenue + institutional + EIS heuristic."""

    def test_qualifying_deal_gets_100(self):
        deal = ScoredDeal(
            entity_id="q1",
            score=0,
            sector="fintech",
            platform="seedrs",
            campaign_date="2020-01-01",
            has_revenue=True,
            has_institutional_coinvestor=True,
            eis_eligible=True,
            outcome="trading",
        )
        result = heuristic_baseline([deal])
        assert result[0].score == 100.0

    def test_missing_revenue_gets_0(self):
        deal = ScoredDeal(
            entity_id="q2",
            score=0,
            sector="fintech",
            platform="seedrs",
            campaign_date="2020-01-01",
            has_revenue=False,
            has_institutional_coinvestor=True,
            eis_eligible=True,
            outcome="trading",
        )
        result = heuristic_baseline([deal])
        assert result[0].score == 0.0

    def test_missing_institutional_gets_0(self):
        deal = ScoredDeal(
            entity_id="q3",
            score=0,
            sector="fintech",
            platform="seedrs",
            campaign_date="2020-01-01",
            has_revenue=True,
            has_institutional_coinvestor=False,
            eis_eligible=True,
            outcome="trading",
        )
        result = heuristic_baseline([deal])
        assert result[0].score == 0.0

    def test_missing_eis_gets_0(self):
        deal = ScoredDeal(
            entity_id="q4",
            score=0,
            sector="fintech",
            platform="seedrs",
            campaign_date="2020-01-01",
            has_revenue=True,
            has_institutional_coinvestor=True,
            eis_eligible=False,
            outcome="trading",
        )
        result = heuristic_baseline([deal])
        assert result[0].score == 0.0

    def test_mixed_deals(self, sample_deals):
        scored = heuristic_baseline(sample_deals)
        for s, orig in zip(scored, sample_deals):
            expected = (
                100.0
                if (orig.has_revenue and orig.has_institutional_coinvestor and orig.eis_eligible)
                else 0.0
            )
            assert s.score == expected


# ------------------------------------------------------------------
# Sector momentum baseline
# ------------------------------------------------------------------


class TestSectorMomentumBaseline:
    """Verify sector exit-rate scoring."""

    def test_uses_lookback_window(self):
        """Only historical deals within the lookback period count."""
        historical = [
            ScoredDeal(
                entity_id="h1", score=0, sector="fintech", platform="seedrs",
                campaign_date="2018-06-01",
                has_revenue=True, has_institutional_coinvestor=True, eis_eligible=True,
                outcome="exited",
            ),
            ScoredDeal(
                entity_id="h2", score=0, sector="fintech", platform="seedrs",
                campaign_date="2018-06-01",
                has_revenue=True, has_institutional_coinvestor=True, eis_eligible=True,
                outcome="failed",
            ),
            # This one is outside the 2-year lookback from 2020-06-01
            ScoredDeal(
                entity_id="h3", score=0, sector="fintech", platform="seedrs",
                campaign_date="2016-01-01",
                has_revenue=True, has_institutional_coinvestor=True, eis_eligible=True,
                outcome="exited",
            ),
        ]
        target = [
            ScoredDeal(
                entity_id="t1", score=0, sector="fintech", platform="seedrs",
                campaign_date="2020-06-01",
                has_revenue=True, has_institutional_coinvestor=True, eis_eligible=True,
                outcome="trading",
            ),
        ]
        scored = sector_momentum_baseline(target, historical, lookback_years=2)
        # Only h1 and h2 are in the window (2018-06-01 to 2020-06-01).
        # 1 exited / 2 total = 50%
        assert scored[0].score == pytest.approx(50.0)

    def test_no_historical_deals_gives_zero(self):
        target = [
            ScoredDeal(
                entity_id="t1", score=0, sector="biotech", platform="seedrs",
                campaign_date="2020-06-01",
                has_revenue=True, has_institutional_coinvestor=True, eis_eligible=True,
                outcome="trading",
            ),
        ]
        scored = sector_momentum_baseline(target, [], lookback_years=2)
        assert scored[0].score == 0.0

    def test_different_sector_not_counted(self):
        """Historical deals from a different sector shouldn't affect the target."""
        historical = [
            ScoredDeal(
                entity_id="h1", score=0, sector="healthtech", platform="seedrs",
                campaign_date="2019-06-01",
                has_revenue=True, has_institutional_coinvestor=True, eis_eligible=True,
                outcome="exited",
            ),
        ]
        target = [
            ScoredDeal(
                entity_id="t1", score=0, sector="fintech", platform="seedrs",
                campaign_date="2020-06-01",
                has_revenue=True, has_institutional_coinvestor=True, eis_eligible=True,
                outcome="trading",
            ),
        ]
        scored = sector_momentum_baseline(target, historical, lookback_years=2)
        assert scored[0].score == 0.0

    def test_all_exited_gives_100(self):
        historical = [
            ScoredDeal(
                entity_id=f"h{i}", score=0, sector="saas", platform="seedrs",
                campaign_date="2019-06-01",
                has_revenue=True, has_institutional_coinvestor=True, eis_eligible=True,
                outcome="exited",
            )
            for i in range(5)
        ]
        target = [
            ScoredDeal(
                entity_id="t1", score=0, sector="saas", platform="seedrs",
                campaign_date="2020-06-01",
                has_revenue=True, has_institutional_coinvestor=True, eis_eligible=True,
                outcome="trading",
            ),
        ]
        scored = sector_momentum_baseline(target, historical, lookback_years=2)
        assert scored[0].score == pytest.approx(100.0)
