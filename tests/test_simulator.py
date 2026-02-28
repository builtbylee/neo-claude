"""Tests for the portfolio simulator."""

from __future__ import annotations

import pytest

from startuplens.backtest.baselines import ScoredDeal
from startuplens.backtest.simulator import (
    InvestorPolicy,
    SimulatedPortfolio,
    simulate_portfolio,
    simulate_walk_forward,
)
from startuplens.backtest.splitter import generate_walk_forward_windows


def _make_deal(
    entity_id: str = "e1",
    score: float = 50.0,
    sector: str = "fintech",
    outcome: str = "trading",
    campaign_date: str = "2020-06-15",
) -> ScoredDeal:
    return ScoredDeal(
        entity_id=entity_id,
        score=score,
        sector=sector,
        platform="seedrs",
        campaign_date=campaign_date,
        has_revenue=True,
        has_institutional_coinvestor=True,
        eis_eligible=True,
        outcome=outcome,
    )


# ------------------------------------------------------------------
# Max investments per year
# ------------------------------------------------------------------


class TestMaxInvestmentsPerYear:
    """Verify the per-year investment cap."""

    def test_selects_at_most_max_per_year(self):
        deals = [_make_deal(entity_id=f"e{i}", score=100 - i, sector=f"s{i}") for i in range(10)]
        policy = InvestorPolicy(max_investments_per_year=2, max_per_sector_per_year=1)
        portfolio = simulate_portfolio(deals, policy)
        assert len(portfolio.selected_deals) == 2

    def test_selects_highest_scored(self):
        deals = [
            _make_deal(entity_id="low", score=10, sector="a"),
            _make_deal(entity_id="high", score=90, sector="b"),
            _make_deal(entity_id="mid", score=50, sector="c"),
        ]
        policy = InvestorPolicy(max_investments_per_year=2, max_per_sector_per_year=1)
        portfolio = simulate_portfolio(deals, policy)
        selected_ids = {d.entity_id for d in portfolio.selected_deals}
        assert "high" in selected_ids
        assert "mid" in selected_ids

    def test_one_per_year_limit(self):
        deals = [_make_deal(entity_id=f"e{i}", score=100 - i) for i in range(5)]
        policy = InvestorPolicy(max_investments_per_year=1)
        portfolio = simulate_portfolio(deals, policy)
        assert len(portfolio.selected_deals) == 1


# ------------------------------------------------------------------
# Max per sector per year
# ------------------------------------------------------------------


class TestMaxPerSectorPerYear:
    """Verify sector diversification constraint."""

    def test_skips_second_deal_in_same_sector(self):
        deals = [
            _make_deal(entity_id="ft1", score=90, sector="fintech"),
            _make_deal(entity_id="ft2", score=80, sector="fintech"),
            _make_deal(entity_id="ht1", score=70, sector="healthtech"),
        ]
        policy = InvestorPolicy(max_investments_per_year=3, max_per_sector_per_year=1)
        portfolio = simulate_portfolio(deals, policy)
        selected_ids = [d.entity_id for d in portfolio.selected_deals]
        assert "ft1" in selected_ids
        assert "ft2" not in selected_ids
        assert "ht1" in selected_ids

    def test_allows_two_per_sector_if_policy_permits(self):
        deals = [
            _make_deal(entity_id="ft1", score=90, sector="fintech"),
            _make_deal(entity_id="ft2", score=80, sector="fintech"),
        ]
        policy = InvestorPolicy(max_investments_per_year=5, max_per_sector_per_year=2)
        portfolio = simulate_portfolio(deals, policy)
        assert len(portfolio.selected_deals) == 2


# ------------------------------------------------------------------
# Failure rate computation
# ------------------------------------------------------------------


class TestFailureRate:
    """Verify failure rate is computed correctly."""

    def test_all_failed(self):
        deals = [
            _make_deal(entity_id="f1", score=90, sector="a", outcome="failed"),
            _make_deal(entity_id="f2", score=80, sector="b", outcome="failed"),
        ]
        policy = InvestorPolicy(max_investments_per_year=2)
        portfolio = simulate_portfolio(deals, policy)
        assert portfolio.failure_rate == pytest.approx(1.0)

    def test_none_failed(self):
        deals = [
            _make_deal(entity_id="t1", score=90, sector="a", outcome="trading"),
            _make_deal(entity_id="t2", score=80, sector="b", outcome="exited"),
        ]
        policy = InvestorPolicy(max_investments_per_year=2)
        portfolio = simulate_portfolio(deals, policy)
        assert portfolio.failure_rate == pytest.approx(0.0)

    def test_partial_failure(self):
        deals = [
            _make_deal(entity_id="f1", score=90, sector="a", outcome="failed"),
            _make_deal(entity_id="t1", score=80, sector="b", outcome="trading"),
        ]
        policy = InvestorPolicy(max_investments_per_year=2)
        portfolio = simulate_portfolio(deals, policy)
        assert portfolio.failure_rate == pytest.approx(0.5)

    def test_empty_portfolio(self):
        portfolio = simulate_portfolio([], InvestorPolicy())
        assert portfolio.failure_rate == 0.0
        assert portfolio.selected_deals == []


# ------------------------------------------------------------------
# Outcomes tracking
# ------------------------------------------------------------------


class TestOutcomesTracking:
    """Verify outcome counting in the portfolio."""

    def test_outcome_counts(self):
        deals = [
            _make_deal(entity_id="e1", score=90, sector="a", outcome="exited"),
            _make_deal(entity_id="e2", score=80, sector="b", outcome="failed"),
        ]
        policy = InvestorPolicy(max_investments_per_year=2)
        portfolio = simulate_portfolio(deals, policy)
        assert portfolio.outcomes["exited"] == 1
        assert portfolio.outcomes["failed"] == 1
        assert portfolio.outcomes["trading"] == 0


# ------------------------------------------------------------------
# Total invested
# ------------------------------------------------------------------


class TestTotalInvested:
    """Verify investment amount calculation."""

    def test_total_invested(self):
        deals = [
            _make_deal(entity_id="e1", score=90, sector="a"),
            _make_deal(entity_id="e2", score=80, sector="b"),
        ]
        policy = InvestorPolicy(max_investments_per_year=2, check_size=5000)
        portfolio = simulate_portfolio(deals, policy)
        assert portfolio.total_invested == pytest.approx(10000.0)


# ------------------------------------------------------------------
# Abstention rate
# ------------------------------------------------------------------


class TestAbstentionRate:
    """Verify abstention rate computation."""

    def test_abstention_when_some_skipped(self):
        # 5 eligible, select 2 -> abstention = 1 - 2/5 = 0.6
        deals = [
            _make_deal(entity_id=f"e{i}", score=100 - i, sector=f"s{i}")
            for i in range(5)
        ]
        policy = InvestorPolicy(max_investments_per_year=2)
        portfolio = simulate_portfolio(deals, policy)
        assert portfolio.abstention_rate == pytest.approx(0.6)


# ------------------------------------------------------------------
# Vintage year filtering
# ------------------------------------------------------------------


class TestVintageYearFiltering:
    """Verify that vintage_year filters deals correctly."""

    def test_filters_to_vintage_year(self):
        deals = [
            _make_deal(entity_id="2020a", score=90, sector="a", campaign_date="2020-03-01"),
            _make_deal(entity_id="2021a", score=80, sector="b", campaign_date="2021-03-01"),
        ]
        policy = InvestorPolicy(max_investments_per_year=5)
        portfolio = simulate_portfolio(deals, policy, vintage_year=2020)
        assert len(portfolio.selected_deals) == 1
        assert portfolio.selected_deals[0].entity_id == "2020a"


# ------------------------------------------------------------------
# Walk-forward simulation
# ------------------------------------------------------------------


class TestSimulateWalkForward:
    """Verify multi-window simulation."""

    def test_returns_one_portfolio_per_window(self):
        windows = generate_walk_forward_windows()
        deals_by_window = {w.label: [] for w in windows}
        policy = InvestorPolicy()
        portfolios = simulate_walk_forward(windows, deals_by_window, policy)
        assert len(portfolios) == len(windows)

    def test_missing_window_returns_empty_portfolio(self):
        windows = generate_walk_forward_windows()
        # Only provide deals for the first window
        deals_by_window = {
            windows[0].label: [
                _make_deal(entity_id="e1", score=90, sector="a"),
            ]
        }
        policy = InvestorPolicy()
        portfolios = simulate_walk_forward(windows, deals_by_window, policy)
        assert len(portfolios[0].selected_deals) == 1
        # Remaining windows should have empty portfolios
        for p in portfolios[1:]:
            assert len(p.selected_deals) == 0
