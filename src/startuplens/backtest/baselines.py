"""Baseline scoring strategies that every model must beat.

Three baselines are implemented:
1. **Random** — deterministic pseudo-random scores (seeded).
2. **Simple heuristic** — has_revenue AND institutional co-investor AND EIS eligible.
3. **Sector momentum** — invest in the sector with the most exits in the prior
   *lookback_years* period.
"""

from __future__ import annotations

import random as _random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date


@dataclass
class ScoredDeal:
    """A deal with an assigned score and metadata needed for portfolio simulation."""

    entity_id: str
    score: float
    sector: str
    platform: str
    campaign_date: str  # ISO-format YYYY-MM-DD
    has_revenue: bool
    has_institutional_coinvestor: bool
    eis_eligible: bool
    outcome: str  # "trading" | "exited" | "failed"


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------

def random_baseline(
    deals: Sequence[ScoredDeal],
    seed: int = 42,
) -> list[ScoredDeal]:
    """Assign a deterministic random score (0-100) to each deal.

    Uses a local RNG seeded with *seed* so results are reproducible
    regardless of global random state.
    """
    rng = _random.Random(seed)
    return [
        ScoredDeal(
            entity_id=d.entity_id,
            score=rng.uniform(0, 100),
            sector=d.sector,
            platform=d.platform,
            campaign_date=d.campaign_date,
            has_revenue=d.has_revenue,
            has_institutional_coinvestor=d.has_institutional_coinvestor,
            eis_eligible=d.eis_eligible,
            outcome=d.outcome,
        )
        for d in deals
    ]


def heuristic_baseline(deals: Sequence[ScoredDeal]) -> list[ScoredDeal]:
    """Score 100 if the deal has revenue, an institutional co-investor, and
    is EIS eligible; 0 otherwise.
    """
    return [
        ScoredDeal(
            entity_id=d.entity_id,
            score=(
                100.0 if (d.has_revenue and d.has_institutional_coinvestor and d.eis_eligible)
                else 0.0
            ),
            sector=d.sector,
            platform=d.platform,
            campaign_date=d.campaign_date,
            has_revenue=d.has_revenue,
            has_institutional_coinvestor=d.has_institutional_coinvestor,
            eis_eligible=d.eis_eligible,
            outcome=d.outcome,
        )
        for d in deals
    ]


def sector_momentum_baseline(
    deals: Sequence[ScoredDeal],
    all_historical_deals: Sequence[ScoredDeal],
    lookback_years: int = 2,
) -> list[ScoredDeal]:
    """Score each deal by its sector's historical exit rate.

    For each deal, look at *all_historical_deals* whose ``campaign_date``
    falls within the *lookback_years* window **before** the deal's own
    campaign date.  The score is the fraction of those same-sector deals
    that exited, multiplied by 100.

    If there are no historical deals in the lookback window for that
    sector, the score is 0.
    """
    scored: list[ScoredDeal] = []

    for deal in deals:
        deal_date = date.fromisoformat(deal.campaign_date)
        lookback_start = date(deal_date.year - lookback_years, deal_date.month, deal_date.day)

        # Count sector outcomes in lookback window
        sector_total = 0
        sector_exited = 0
        for h in all_historical_deals:
            h_date = date.fromisoformat(h.campaign_date)
            if h.sector == deal.sector and lookback_start <= h_date < deal_date:
                sector_total += 1
                if h.outcome == "exited":
                    sector_exited += 1

        exit_rate = (sector_exited / sector_total) if sector_total > 0 else 0.0

        scored.append(
            ScoredDeal(
                entity_id=deal.entity_id,
                score=exit_rate * 100,
                sector=deal.sector,
                platform=deal.platform,
                campaign_date=deal.campaign_date,
                has_revenue=deal.has_revenue,
                has_institutional_coinvestor=deal.has_institutional_coinvestor,
                eis_eligible=deal.eis_eligible,
                outcome=deal.outcome,
            )
        )

    return scored
