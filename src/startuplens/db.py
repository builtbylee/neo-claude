"""PostgreSQL database connection via psycopg3."""

import psycopg
from psycopg import sql
from psycopg.errors import ObjectNotInPrerequisiteState
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row

from startuplens.config import Settings


def get_connection(settings: Settings | None = None) -> psycopg.Connection:
    """Open a synchronous connection to PostgreSQL with dict row factory."""
    if settings is None:
        from startuplens.config import get_settings
        settings = get_settings()

    return psycopg.connect(settings.database_url, row_factory=dict_row)


def execute_query(conn: psycopg.Connection, query: str, params: tuple = ()) -> list[dict]:
    """Execute a query and return all rows as dicts."""
    with conn.cursor() as cur:
        cur.execute(query, params)
        if cur.description:
            return cur.fetchall()
        return []


def execute_many(conn: psycopg.Connection, query: str, params_list: list[tuple]) -> int:
    """Execute a parameterised query for each set of params. Returns row count."""
    with conn.cursor() as cur:
        cur.executemany(query, params_list)
        return cur.rowcount


def refresh_matview(conn: psycopg.Connection, name: str = "training_features_wide") -> None:
    """Refresh a materialized view so it reflects current feature_store data.

    Uses CONCURRENTLY when possible (requires a unique index on the matview).
    """
    previous_autocommit = conn.autocommit
    identifier = sql.Identifier(name)

    try:
        # REFRESH ... CONCURRENTLY must run outside an explicit transaction.
        if not conn.autocommit:
            if conn.info.transaction_status != TransactionStatus.IDLE:
                conn.commit()
            conn.autocommit = True

        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("REFRESH MATERIALIZED VIEW CONCURRENTLY {}").format(identifier),
            )
    except ObjectNotInPrerequisiteState:
        # Fallback for matviews that don't have a qualifying unique index.
        if conn.autocommit != previous_autocommit:
            conn.autocommit = previous_autocommit
        with conn.cursor() as cur:
            cur.execute(sql.SQL("REFRESH MATERIALIZED VIEW {}").format(identifier))
        conn.commit()
    finally:
        if conn.autocommit != previous_autocommit:
            conn.autocommit = previous_autocommit
