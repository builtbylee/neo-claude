"""Probabilistic (fuzzy) entity resolution using the dedupe library.

Provides model training, candidate pair discovery, and entity merging
for records that cannot be matched deterministically.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import dedupe
import psycopg

from startuplens.db import execute_query

# ---------------------------------------------------------------------------
# Field definition shared by training and matching
# ---------------------------------------------------------------------------

DEDUPE_FIELDS: list[dict[str, str]] = [
    {"field": "name", "type": "String"},
    {"field": "country", "type": "Exact"},
]


# ---------------------------------------------------------------------------
# Training data extraction
# ---------------------------------------------------------------------------

def build_training_pairs(conn: psycopg.Connection) -> list[dict[str, str]]:
    """Extract name/country pairs from canonical_entities for dedupe training.

    Returns a list of dicts, each with ``name`` and ``country`` keys,
    suitable for building a dedupe training set.
    """
    rows = execute_query(
        conn,
        "SELECT id::text AS entity_id, primary_name, country FROM canonical_entities",
    )
    return [
        {"entity_id": r["entity_id"], "name": r["primary_name"], "country": r["country"]}
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Model training / loading
# ---------------------------------------------------------------------------

def train_dedupe_model(
    pairs: list[dict[str, str]],
    settings_path: Path | None = None,
) -> dedupe.Dedupe:
    """Train a new dedupe model, or load an existing one from *settings_path*.

    Parameters
    ----------
    pairs:
        Records containing at least ``name`` and ``country`` keys.
    settings_path:
        If the file exists, the model is loaded from it.  Otherwise a
        new model is trained and (if the path is provided) saved there.

    Returns
    -------
    dedupe.Dedupe
        A trained deduplication model.
    """
    if settings_path and settings_path.exists():
        with open(settings_path, "rb") as f:
            return dedupe.StaticDedupe(f)  # type: ignore[return-value]

    deduper = dedupe.Dedupe(DEDUPE_FIELDS)

    # Convert list to dict keyed by index for dedupe API
    data: dict[str | int, dict[str, str]] = {
        i: {"name": p["name"], "country": p["country"]}
        for i, p in enumerate(pairs)
    }

    deduper.prepare_training(data)
    deduper.train()

    if settings_path:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        with open(settings_path, "wb") as f:
            deduper.write_settings(f)

    return deduper


# ---------------------------------------------------------------------------
# Match discovery
# ---------------------------------------------------------------------------

def find_probable_matches(
    model: Any,
    records: dict[str | int, dict[str, str]],
) -> list[tuple[str, str, float]]:
    """Run the trained dedupe model over *records* to find probable duplicates.

    Parameters
    ----------
    model:
        A trained ``dedupe.Dedupe`` or ``dedupe.StaticDedupe`` instance.
    records:
        Dict mapping record ID to ``{"name": ..., "country": ...}`` dicts.

    Returns
    -------
    list[tuple[str, str, float]]
        Triples of ``(id_a, id_b, confidence)`` where confidence is in
        [0, 1].
    """
    clustered = model.partition(records, threshold=0.5)

    matches: list[tuple[str, str, float]] = []
    for cluster_ids, scores in clustered:
        if len(cluster_ids) < 2:
            continue
        # For each pair within a cluster, emit a match tuple
        avg_score = float(sum(scores)) / len(scores)
        ids = list(cluster_ids)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                id_a = str(ids[i])
                id_b = str(ids[j])
                matches.append((id_a, id_b, avg_score))

    return matches


# ---------------------------------------------------------------------------
# Entity merging
# ---------------------------------------------------------------------------

def merge_entities(
    conn: psycopg.Connection,
    keep_id: str,
    merge_id: str,
) -> None:
    """Merge two canonical entities by reassigning all links to *keep_id*.

    All ``entity_links`` rows pointing at *merge_id* are updated to
    point at *keep_id*.  The canonical_entities row for *merge_id* is
    then deleted.

    Parameters
    ----------
    keep_id:
        UUID of the entity to keep.
    merge_id:
        UUID of the entity to absorb and remove.
    """
    execute_query(
        conn,
        "UPDATE entity_links SET entity_id = %s WHERE entity_id = %s",
        (keep_id, merge_id),
    )
    execute_query(
        conn,
        "DELETE FROM canonical_entities WHERE id = %s",
        (merge_id,),
    )
