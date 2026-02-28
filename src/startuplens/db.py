"""PostgreSQL database connection via psycopg3."""

import psycopg
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
