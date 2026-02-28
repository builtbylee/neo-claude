"""Shared fixtures for backtest tests."""

from __future__ import annotations

import pytest

from startuplens.backtest.baselines import ScoredDeal

# Sectors and outcomes used to build varied synthetic data
_SECTORS = ["fintech", "healthtech", "saas", "edtech", "cleantech"]
_OUTCOMES = ["trading", "exited", "failed"]
_PLATFORMS = ["seedrs", "crowdcube", "republic", "wefunder"]


@pytest.fixture()
def sample_deals() -> list[ScoredDeal]:
    """Return 20 synthetic ScoredDeal objects with varied properties.

    The deals span campaign dates from 2017 to 2024, rotate through
    five sectors and four platforms, and have a mix of outcomes and
    boolean flags designed to exercise all baseline and simulator logic.
    """
    deals: list[ScoredDeal] = []
    for i in range(20):
        year = 2017 + (i % 8)  # 2017-2024 cycling
        sector = _SECTORS[i % len(_SECTORS)]
        platform = _PLATFORMS[i % len(_PLATFORMS)]
        outcome = _OUTCOMES[i % len(_OUTCOMES)]

        # Stagger boolean flags so we get a mix of heuristic-qualifying
        # and non-qualifying deals
        has_revenue = i % 3 != 0
        has_institutional = i % 4 != 0
        eis_eligible = i % 5 != 0

        deals.append(
            ScoredDeal(
                entity_id=f"entity-{i:03d}",
                score=0.0,  # baselines/model will overwrite
                sector=sector,
                platform=platform,
                campaign_date=f"{year}-06-15",
                has_revenue=has_revenue,
                has_institutional_coinvestor=has_institutional,
                eis_eligible=eis_eligible,
                outcome=outcome,
            )
        )
    return deals
