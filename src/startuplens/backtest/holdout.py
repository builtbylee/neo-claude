"""Test-set quarantine manager.

Ensures holdout entities are physically separated before model training begins.
Once quarantined, holdout records are immutable — they can only be read, never
modified or re-assigned.
"""

from __future__ import annotations

import uuid
from typing import Any

from startuplens.db import execute_many, execute_query


def quarantine_holdout(
    conn: Any,
    entity_ids: list[str],
    window_label: str,
    company_ids: list[str | None] | None = None,
) -> int:
    """Insert entities into the backtest_holdout table.

    Parameters
    ----------
    conn:
        Database connection.
    entity_ids:
        UUIDs of canonical entities to quarantine.
    window_label:
        Human-readable window identifier, e.g. ``"2023-2025"``.
    company_ids:
        Optional parallel list of company UUIDs.  If ``None``, all
        company_id values are stored as NULL.

    Returns
    -------
    int
        Number of rows inserted (duplicates are silently skipped).
    """
    if not entity_ids:
        return 0

    if company_ids is None:
        company_ids = [None] * len(entity_ids)

    rows = [
        (str(uuid.uuid4()), eid, cid, window_label)
        for eid, cid in zip(entity_ids, company_ids)
    ]

    query = """
        INSERT INTO backtest_holdout (id, entity_id, company_id, holdout_window)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT DO NOTHING
    """
    return execute_many(conn, query, rows)


def get_holdout_entity_ids(conn: Any, window_label: str) -> list[str]:
    """Return all entity_ids quarantined for a given window."""
    rows = execute_query(
        conn,
        "SELECT entity_id FROM backtest_holdout WHERE holdout_window = %s",
        (window_label,),
    )
    return [str(r["entity_id"]) for r in rows]


def is_entity_held_out(conn: Any, entity_id: str, window_label: str) -> bool:
    """Check if a specific entity is in the holdout set for a window."""
    rows = execute_query(
        conn,
        "SELECT 1 FROM backtest_holdout WHERE entity_id = %s AND holdout_window = %s LIMIT 1",
        (entity_id, window_label),
    )
    return len(rows) > 0


def filter_training_entities(
    conn: Any,
    entity_ids: list[str],
    window_label: str,
) -> list[str]:
    """Remove holdout entities from a list, returning only training-safe IDs."""
    holdout_ids = set(get_holdout_entity_ids(conn, window_label))
    return [eid for eid in entity_ids if eid not in holdout_ids]


def get_holdout_summary(conn: Any) -> list[dict]:
    """Return a summary of holdout windows and their entity counts."""
    return execute_query(
        conn,
        """
        SELECT holdout_window, COUNT(*) AS entity_count, MIN(created_at) AS created_at
        FROM backtest_holdout
        GROUP BY holdout_window
        ORDER BY holdout_window
        """,
    )
