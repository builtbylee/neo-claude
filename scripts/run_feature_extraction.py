#!/usr/bin/env python3
"""CLI script to extract features for all entities and write to the feature store."""

from __future__ import annotations

from datetime import date

import structlog
import typer

from startuplens.config import get_settings
from startuplens.db import execute_query, get_connection
from startuplens.feature_store.extractors import (
    extract_campaign_features,
    extract_company_features,
    extract_financial_features,
    extract_market_regime_features,
    extract_regulatory_features,
    extract_team_features,
    extract_terms_features,
)
from startuplens.feature_store.store import write_features_batch

logger = structlog.get_logger(__name__)
app = typer.Typer()

ALL_EXTRACTORS = [
    ("campaign", extract_campaign_features),
    ("company", extract_company_features),
    ("financial", extract_financial_features),
    ("team", extract_team_features),
    ("terms", extract_terms_features),
    ("regulatory", extract_regulatory_features),
    ("market_regime", extract_market_regime_features),
]


@app.command()
def main(
    as_of_date: str = typer.Option(
        None, help="As-of date (YYYY-MM-DD). Defaults to today."
    ),
    label_tier: int = typer.Option(2, help="Default label quality tier"),
    with_outcomes_only: bool = typer.Option(
        False, "--with-outcomes-only",
        help="Only extract features for entities that have crowdfunding outcomes",
    ),
) -> None:
    """Extract features from all entities and write to the feature store."""
    target_date = date.fromisoformat(as_of_date) if as_of_date else date.today()

    settings = get_settings()
    conn = get_connection(settings)

    try:
        entities = execute_query(
            conn,
            """
            SELECT DISTINCT ON (ce.id)
                ce.id::text AS entity_id,
                c.name,
                c.country,
                c.sector,
                c.source,
                c.source_id,
                c.sic_code,
                c.founding_date,
                c.founding_date AS incorporation_date,
                co.campaign_date,
                co.funding_target,
                co.amount_raised AS outcome_amount_raised,
                co.overfunding_ratio,
                co.equity_offered AS equity_offered_pct,
                co.pre_money_valuation,
                co.investor_count,
                co.funding_velocity_days,
                co.eis_seis_eligible,
                co.qualified_institutional_coinvestor AS qualified_institutional,
                co.prior_vc_backing,
                co.accelerator_alumni,
                co.accelerator_name,
                co.founder_count,
                co.founder_domain_experience_years,
                co.founder_prior_exits,
                co.had_revenue,
                co.revenue_at_raise,
                co.revenue_model AS revenue_model_type,
                co.company_age_at_raise_months,
                co.stage_bucket,
                co.outcome,
                co.label_quality_tier,
                fr.round_date,
                fr.round_type,
                fr.instrument_type,
                fr.amount_raised AS round_amount_raised,
                fr.platform
            FROM canonical_entities ce
            JOIN entity_links el ON el.entity_id = ce.id
            JOIN companies c
                ON c.id::text = el.source_identifier AND el.source = c.source
            LEFT JOIN crowdfunding_outcomes co ON co.company_id = c.id
            LEFT JOIN funding_rounds fr ON fr.company_id = c.id"""
            + (" WHERE co.id IS NOT NULL" if with_outcomes_only else "")
            + """
            ORDER BY ce.id, el.confidence DESC
            """,
        )

        logger.info("entities_to_process", count=len(entities))
        total_written = 0

        for entity in entities:
            entity_id = entity["entity_id"]
            # Use campaign/filing date as as_of_date for temporal correctness
            entity_date = target_date
            for date_field in ("campaign_date", "round_date"):
                if entity.get(date_field):
                    entity_date = entity[date_field]
                    break
            for family_name, extractor in ALL_EXTRACTORS:
                features = extractor(entity)
                count = write_features_batch(
                    conn, entity_id, features, entity_date,
                    source=f"extractor_{family_name}",
                    label_tier=label_tier,
                )
                total_written += count

        conn.commit()
        logger.info("feature_extraction_complete", total_written=total_written)

    finally:
        conn.close()


if __name__ == "__main__":
    app()
