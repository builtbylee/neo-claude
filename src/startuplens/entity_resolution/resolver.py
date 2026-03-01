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
# Bulk entity creation (for large single-source datasets like Form D)
# ---------------------------------------------------------------------------

def bulk_create_entities(
    conn: psycopg.Connection,
    records: list[dict],
    *,
    batch_size: int = 500,
) -> dict[str, int]:
    """Create canonical entities in bulk for records that each become their own entity.

    Unlike ``run_entity_resolution``, this skips cross-source matching — each
    record gets a new canonical entity and a corresponding entity link.  This
    is appropriate for large single-source datasets (e.g., Form D companies)
    where each record is already known to be unique within its source.

    Parameters
    ----------
    conn:
        Database connection.
    records:
        List of dicts with ``name``, ``country``, ``source``,
        ``source_identifier`` keys.
    batch_size:
        Number of records per multi-row INSERT.

    Returns
    -------
    dict
        ``{"created": int, "skipped": int, "total": int}``
    """
    import uuid

    from startuplens.entity_resolution.deterministic import normalize_name

    created = 0
    skipped = 0

    for i in range(0, len(records), batch_size):
        batch = records[i : i + batch_size]

        # Pre-check which source_identifiers already have links
        existing = set()
        if batch:
            from startuplens.db import execute_query

            placeholders = ", ".join(
                ["(%s, %s)"] * len(batch)
            )
            params: list[str] = []
            for r in batch:
                params.extend([r["source"], r["source_identifier"]])

            rows = execute_query(
                conn,
                f"""
                SELECT source, source_identifier
                FROM entity_links
                WHERE (source, source_identifier) IN ({placeholders})
                """,
                tuple(params),
            )
            existing = {(r["source"], r["source_identifier"]) for r in rows}

        # Filter to new records only
        new_records = [
            r for r in batch
            if (r["source"], r["source_identifier"]) not in existing
        ]
        skipped += len(batch) - len(new_records)

        if not new_records:
            continue

        # Generate UUIDs and normalize names
        entities = []
        for r in new_records:
            entity_id = str(uuid.uuid4())
            link_id = str(uuid.uuid4())
            norm_name = normalize_name(r["name"])
            country = r.get("country", "US").lower()
            entities.append({
                "entity_id": entity_id,
                "link_id": link_id,
                "norm_name": norm_name,
                "country": country,
                "source": r["source"],
                "source_identifier": r["source_identifier"],
                "source_name": r["name"],
            })

        # Bulk INSERT into canonical_entities
        ce_placeholders = ", ".join(
            ["(%s, %s, %s)"] * len(entities)
        )
        ce_params: list[str] = []
        for e in entities:
            ce_params.extend([e["entity_id"], e["norm_name"], e["country"]])

        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO canonical_entities (id, primary_name, country)
                VALUES {ce_placeholders}
                ON CONFLICT (id) DO NOTHING
                """,
                tuple(ce_params),
            )

        # Bulk INSERT into entity_links
        el_placeholders = ", ".join(
            ["(%s, %s, %s, %s, %s, %s, %s)"] * len(entities)
        )
        el_params: list[str] = []
        for e in entities:
            el_params.extend([
                e["link_id"], e["entity_id"], e["source"],
                e["source_identifier"], e["source_name"],
                "exact_id", "100",
            ])

        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO entity_links
                    (id, entity_id, source, source_identifier, source_name,
                     match_method, confidence)
                VALUES {el_placeholders}
                ON CONFLICT (id) DO NOTHING
                """,
                tuple(el_params),
            )

        created += len(entities)

        if (i + batch_size) % 5000 == 0:
            conn.commit()

    conn.commit()
    return {"created": created, "skipped": skipped, "total": len(records)}


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
