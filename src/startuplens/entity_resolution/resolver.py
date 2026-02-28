"""Entity resolution orchestrator.

Runs the full deterministic-then-probabilistic pipeline to map incoming
records to canonical entities.
"""

from __future__ import annotations

from pathlib import Path

import psycopg

from startuplens.entity_resolution.deterministic import (
    create_canonical_entity,
    link_entity,
    match_by_legal_name,
    match_by_source_id,
)
from startuplens.entity_resolution.probabilistic import (
    build_training_pairs,
    find_probable_matches,
    merge_entities,
    train_dedupe_model,
)


def resolve_entity(
    conn: psycopg.Connection,
    name: str,
    country: str,
    source: str,
    source_identifier: str,
) -> str:
    """Resolve a single record to a canonical entity.

    Resolution order:
      1. Exact source-ID match (highest confidence).
      2. Deterministic name + country match.
      3. Create a new canonical entity if neither match succeeds.

    In all cases, an ``entity_links`` row is ensured for the
    (source, source_identifier) pair.

    Returns the ``entity_id`` (UUID string).
    """
    # 1. Source-ID match
    entity_id = match_by_source_id(conn, source, source_identifier)
    if entity_id is not None:
        return entity_id

    # 2. Name + country match
    entity_id = match_by_legal_name(conn, name, country)
    if entity_id is not None:
        link_entity(
            conn,
            entity_id,
            source=source,
            source_identifier=source_identifier,
            confidence=90,
            match_method="deterministic",
            source_name=name,
        )
        return entity_id

    # 3. Create new
    entity_id = create_canonical_entity(conn, name, country)
    link_entity(
        conn,
        entity_id,
        source=source,
        source_identifier=source_identifier,
        confidence=100,
        match_method="exact_id",
        source_name=name,
    )
    return entity_id


# ---------------------------------------------------------------------------
# Batch deterministic resolution
# ---------------------------------------------------------------------------

def run_entity_resolution(
    conn: psycopg.Connection,
    records: list[dict],
) -> dict[str, int]:
    """Resolve a batch of records deterministically.

    Each dict in *records* must contain:
      - ``name``
      - ``country``
      - ``source``
      - ``source_identifier``

    Returns
    -------
    dict
        ``{"matched": int, "created": int, "total": int}``
    """
    matched = 0
    created = 0

    for rec in records:
        # Check if a link already exists (source-ID match) or a name match exists
        existing = match_by_source_id(conn, rec["source"], rec["source_identifier"])
        if existing is None:
            existing = match_by_legal_name(conn, rec["name"], rec["country"])

        if existing is not None:
            matched += 1
        else:
            created += 1

        resolve_entity(
            conn,
            name=rec["name"],
            country=rec["country"],
            source=rec["source"],
            source_identifier=rec["source_identifier"],
        )

    return {"matched": matched, "created": created, "total": len(records)}


# ---------------------------------------------------------------------------
# Probabilistic pass
# ---------------------------------------------------------------------------

def run_probabilistic_pass(
    conn: psycopg.Connection,
    settings_path: Path | None = None,
    confidence_threshold: float = 0.85,
) -> dict[str, int]:
    """Run dedupe-based probabilistic matching over all canonical entities.

    Entities with match confidence >= *confidence_threshold* are merged.

    Returns
    -------
    dict
        ``{"pairs_found": int, "merged": int, "below_threshold": int}``
    """
    pairs = build_training_pairs(conn)

    if len(pairs) < 2:
        return {"pairs_found": 0, "merged": 0, "below_threshold": 0}

    model = train_dedupe_model(pairs, settings_path)

    # Build records dict keyed by entity_id
    records: dict[str | int, dict[str, str]] = {
        p["entity_id"]: {"name": p["name"], "country": p["country"]}
        for p in pairs
    }

    matches = find_probable_matches(model, records)

    merged = 0
    below_threshold = 0

    for id_a, id_b, confidence in matches:
        if confidence >= confidence_threshold:
            merge_entities(conn, keep_id=id_a, merge_id=id_b)
            merged += 1
        else:
            below_threshold += 1

    return {
        "pairs_found": len(matches),
        "merged": merged,
        "below_threshold": below_threshold,
    }
