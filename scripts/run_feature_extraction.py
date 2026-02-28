#!/usr/bin/env python3
"""CLI script to extract features for all entities and write to the feature store."""

from __future__ import annotations

from datetime import date

import typer
import structlog

from startuplens.config import get_settings
from startuplens.db import get_connection, execute_query
from startuplens.feature_store.store import write_features_batch
from startuplens.feature_store.extractors import (
    extract_campaign_features,
    extract_company_features,
    extract_financial_features,
    extract_market_regime_features,
    extract_regulatory_features,
    extract_team_features,
    extract_terms_features,
)

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
) -> None:
    """Extract features from all entities and write to the feature store."""
    target_date = date.fromisoformat(as_of_date) if as_of_date else date.today()

    settings = get_settings()
    conn = get_connection(settings)

    try:
        entities = execute_query(
            conn,
            """
            SELECT
                ce.id::text AS entity_id,
                c.*
            FROM canonical_entities ce
            JOIN entity_links el ON el.entity_id = ce.id
            JOIN companies c ON c.id::text = el.source_identifier AND el.source = 'companies'
            """,
        )

        logger.info("entities_to_process", count=len(entities))
        total_written = 0

        for entity in entities:
            entity_id = entity["entity_id"]
            for family_name, extractor in ALL_EXTRACTORS:
                features = extractor(entity)
                count = write_features_batch(
                    conn, entity_id, features, target_date,
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
