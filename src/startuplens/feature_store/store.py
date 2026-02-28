"""Core as-of feature store with temporal correctness.

Provides read/write operations for the feature_store table with
point-in-time correct queries to prevent data leakage in training.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import psycopg

from startuplens.feature_store.registry import get_feature, is_valid_feature


def validate_feature_write(feature_name: str, value: Any) -> bool:
    """Check that feature_name is in registry and value matches expected dtype.

    Returns True if the write is valid, False otherwise.
    """
    if not is_valid_feature(feature_name):
        return False

    feat = get_feature(feature_name)

    # None values are always allowed (missing data)
    if value is None:
        return True

    if feat.dtype == "numeric":
        return isinstance(value, (int, float))
    if feat.dtype == "boolean":
        return isinstance(value, bool)
    if feat.dtype == "categorical":
        return isinstance(value, str)

    return False


def write_feature(
    conn: psycopg.Connection,
    entity_id: str,
    feature_name: str,
    value: Any,
    as_of_date: date,
    source: str,
    label_tier: int = 3,
) -> None:
    """Write a single feature value to the feature store.

    Validates the feature name against the registry and upserts into the
    feature_store table.  Values are stored as JSONB ``{"value": X}``.

    Raises:
        ValueError: If feature_name is not in the registry.
    """
    if not is_valid_feature(feature_name):
        msg = f"Unknown feature: {feature_name!r}"
        raise ValueError(msg)

    feat = get_feature(feature_name)
    feature_value = json.dumps({"value": value})

    sql = """
        INSERT INTO feature_store
            (entity_id, as_of_date, feature_family, feature_name, feature_value,
             source, label_quality_tier)
        VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s)
        ON CONFLICT (entity_id, as_of_date, feature_family, feature_name)
        DO UPDATE SET
            feature_value = EXCLUDED.feature_value,
            source = EXCLUDED.source,
            label_quality_tier = EXCLUDED.label_quality_tier,
            created_at = now()
    """

    with conn.cursor() as cur:
        cur.execute(
            sql,
            (entity_id, as_of_date, feat.family, feature_name, feature_value, source, label_tier),
        )


def write_features_batch(
    conn: psycopg.Connection,
    entity_id: str,
    features: dict[str, Any],
    as_of_date: date,
    source: str,
    label_tier: int = 3,
) -> int:
    """Batch-write multiple features for the same entity and date.

    Returns the number of features written. Skips features with None values.

    Raises:
        ValueError: If any feature name is not in the registry.
    """
    unknown = [name for name in features if not is_valid_feature(name)]
    if unknown:
        msg = f"Unknown features: {unknown}"
        raise ValueError(msg)

    sql = """
        INSERT INTO feature_store
            (entity_id, as_of_date, feature_family, feature_name, feature_value,
             source, label_quality_tier)
        VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s)
        ON CONFLICT (entity_id, as_of_date, feature_family, feature_name)
        DO UPDATE SET
            feature_value = EXCLUDED.feature_value,
            source = EXCLUDED.source,
            label_quality_tier = EXCLUDED.label_quality_tier,
            created_at = now()
    """

    params_list: list[tuple] = []
    for name, value in features.items():
        if value is None:
            continue
        feat = get_feature(name)
        feature_value = json.dumps({"value": value})
        params_list.append(
            (entity_id, as_of_date, feat.family, name, feature_value, source, label_tier)
        )

    if not params_list:
        return 0

    with conn.cursor() as cur:
        cur.executemany(sql, params_list)

    return len(params_list)


def read_features_as_of(
    conn: psycopg.Connection,
    entity_id: str,
    as_of_date: date,
) -> dict[str, Any]:
    """Read all features for an entity as of a given date.

    For each feature, returns the most recent value on or before ``as_of_date``.
    This is the key temporal correctness query: we never look into the future.

    Returns:
        Dict mapping feature_name -> value (extracted from JSONB).
    """
    sql = """
        SELECT DISTINCT ON (feature_name)
            feature_name,
            feature_value
        FROM feature_store
        WHERE entity_id = %s
          AND as_of_date <= %s
        ORDER BY feature_name, as_of_date DESC
    """

    result: dict[str, Any] = {}
    with conn.cursor() as cur:
        cur.execute(sql, (entity_id, as_of_date))
        if cur.description:
            for row in cur.fetchall():
                feature_value = row["feature_value"]
                if isinstance(feature_value, str):
                    feature_value = json.loads(feature_value)
                result[row["feature_name"]] = feature_value.get("value")

    return result


def read_training_matrix(
    conn: psycopg.Connection,
    as_of_date: date,
    min_label_tier: int = 2,
) -> list[dict]:
    """Read wide-format training data for all entities as of a date.

    Returns one row per entity with all features pivoted into columns.
    Only includes rows where label_quality_tier <= min_label_tier.

    Args:
        conn: Database connection.
        as_of_date: Point-in-time cutoff date.
        min_label_tier: Maximum tier to include (1 = strictest, 3 = all).

    Returns:
        List of dicts, each with entity_id + all feature columns.
    """
    # First, get the most recent feature values per entity/feature as of the date
    sql = """
        WITH ranked AS (
            SELECT
                entity_id,
                feature_name,
                feature_value,
                label_quality_tier,
                ROW_NUMBER() OVER (
                    PARTITION BY entity_id, feature_name
                    ORDER BY as_of_date DESC
                ) AS rn
            FROM feature_store
            WHERE as_of_date <= %s
              AND label_quality_tier <= %s
        )
        SELECT entity_id, feature_name, feature_value
        FROM ranked
        WHERE rn = 1
        ORDER BY entity_id, feature_name
    """

    rows_by_entity: dict[str, dict[str, Any]] = {}
    with conn.cursor() as cur:
        cur.execute(sql, (as_of_date, min_label_tier))
        if cur.description:
            for row in cur.fetchall():
                eid = str(row["entity_id"])
                if eid not in rows_by_entity:
                    rows_by_entity[eid] = {"entity_id": eid}
                feature_value = row["feature_value"]
                if isinstance(feature_value, str):
                    feature_value = json.loads(feature_value)
                rows_by_entity[eid][row["feature_name"]] = feature_value.get("value")

    return list(rows_by_entity.values())
