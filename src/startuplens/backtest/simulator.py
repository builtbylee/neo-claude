"""Portfolio simulator with investor policy constraints.

Simulates realistic investment decisions: rank deals by score, apply
policy constraints (max investments per year, max per sector), and
measure portfolio-level outcomes.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from startuplens.backtest.baselines import ScoredDeal
from startuplens.backtest.splitter import TimeWindow


@dataclass
class InvestorPolicy:
    """Configurable constraints for portfolio construction."""

    max_investments_per_year: int = 2
    check_size: float = 10_000.0
    max_per_sector_per_year: int = 1


@dataclass
class SimulatedPortfolio:
    """Result of simulating a portfolio for one vintage period."""

    selected_deals: list[ScoredDeal] = field(default_factory=list)
    total_invested: float = 0.0
    outcomes: dict[str, int] = field(default_factory=lambda: {
        "trading": 0,
        "exited": 0,
        "failed": 0,
    })
    moic: float | None = None
    failure_rate: float = 0.0
    abstention_rate: float = 0.0


# ---------------------------------------------------------------------------
# Single-vintage simulation
# ---------------------------------------------------------------------------

def simulate_portfolio(
    scored_deals: Sequence[ScoredDeal],
    policy: InvestorPolicy,
    vintage_year: int | None = None,
) -> SimulatedPortfolio:
    """Simulate portfolio construction for a single vintage period.

    Parameters
    ----------
    scored_deals:
        Deals available for selection, each carrying a ``score``.
    policy:
        Investor constraints to enforce.
    vintage_year:
        If provided, only deals whose ``campaign_date`` year matches
        *vintage_year* are considered.  If ``None``, all deals are
        eligible.

    Process
    -------
    1. Filter to *vintage_year* if specified.
    2. Sort by score descending.
    3. Greedily select the top deals that satisfy:
       - No more than ``policy.max_investments_per_year`` total.
       - No more than ``policy.max_per_sector_per_year`` per sector.
    4. Compute portfolio-level metrics.
    """
    # Filter to vintage year if requested
    eligible = list(scored_deals)
    if vintage_year is not None:
        eligible = [
            d for d in eligible
            if date.fromisoformat(d.campaign_date).year == vintage_year
        ]

    total_eligible = len(eligible)

    # Sort by score descending (stable sort — preserves insertion order for ties)
    eligible.sort(key=lambda d: d.score, reverse=True)

    selected: list[ScoredDeal] = []
    sector_counts: Counter[str] = Counter()

    for deal in eligible:
        if len(selected) >= policy.max_investments_per_year:
            break
        if sector_counts[deal.sector] >= policy.max_per_sector_per_year:
            continue
        selected.append(deal)
        sector_counts[deal.sector] += 1

    # Compute outcomes
    outcomes: dict[str, int] = {"trading": 0, "exited": 0, "failed": 0}
    for deal in selected:
        if deal.outcome in outcomes:
            outcomes[deal.outcome] += 1

    n_selected = len(selected)
    total_invested = n_selected * policy.check_size

    # Exclude unknown outcomes from failure rate calculation
    n_with_known_outcome = outcomes["trading"] + outcomes["exited"] + outcomes["failed"]
    failure_rate = (
        outcomes["failed"] / n_with_known_outcome
        if n_with_known_outcome > 0
        else 0.0
    )
    abstention_rate = (
        1.0 - (n_selected / total_eligible) if total_eligible > 0 else 0.0
    )

    # MOIC is not computable from outcome labels alone (no return amounts).
    # We leave it as None; the caller (metric layer) may inject a MOIC value
    # derived from a returns model.
    moic = None

    return SimulatedPortfolio(
        selected_deals=selected,
        total_invested=total_invested,
        outcomes=outcomes,
        moic=moic,
        failure_rate=failure_rate,
        abstention_rate=abstention_rate,
    )


# ---------------------------------------------------------------------------
# Walk-forward simulation across multiple windows
# ---------------------------------------------------------------------------

def simulate_walk_forward(
    windows: Sequence[TimeWindow],
    deals_by_window: dict[str, list[ScoredDeal]],
    policy: InvestorPolicy,
) -> list[SimulatedPortfolio]:
    """Run :func:`simulate_portfolio` for each walk-forward window.

    Parameters
    ----------
    windows:
        Walk-forward time windows (from :func:`generate_walk_forward_windows`).
    deals_by_window:
        Mapping from ``window.label`` to the scored test-set deals for
        that window.
    policy:
        Investor constraints applied uniformly across all windows.

    Returns
    -------
    list[SimulatedPortfolio]
        One portfolio per window, in the same order as *windows*.
    """
    portfolios: list[SimulatedPortfolio] = []
    for window in windows:
        test_deals = deals_by_window.get(window.label, [])
        portfolio = simulate_portfolio(test_deals, policy)
        portfolios.append(portfolio)
    return portfolios


# ---------------------------------------------------------------------------
# Portfolio quality scoring
# ---------------------------------------------------------------------------

def deal_quality_score(deal: ScoredDeal) -> float:
    """Compute a quality score for a single deal based on outcome and revenue growth.

    Scoring:
        - failed → 0.0
        - trading/exited, no growth data → 1.0
        - trading/exited, revenue declining → 0.5
        - trading/exited, revenue flat (|growth| < 10%) → 1.0
        - trading/exited, revenue growing → 1.0 + min(growth, 2.0)  (capped at 3.0)
    """
    if deal.outcome == "failed":
        return 0.0

    if deal.revenue_growth is None:
        return 1.0

    g = float(deal.revenue_growth)
    if g < -0.1:
        return 0.5
    if g <= 0.1:
        return 1.0
    return 1.0 + min(g, 2.0)


def compute_portfolio_quality(portfolio: SimulatedPortfolio) -> float:
    """Compute average quality score for a simulated portfolio's selected deals."""
    if not portfolio.selected_deals:
        return 0.0
    scores = [deal_quality_score(d) for d in portfolio.selected_deals]
    return sum(scores) / len(scores)
