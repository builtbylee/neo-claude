#!/usr/bin/env python3
"""Apply SQL migrations in order with checksum tracking."""

from __future__ import annotations

import hashlib
from pathlib import Path

import structlog
import typer
from psycopg.errors import DuplicateColumn, DuplicateObject, DuplicateTable

from startuplens.config import get_settings
from startuplens.db import execute_query, get_connection

logger = structlog.get_logger(__name__)
app = typer.Typer()


def _checksum(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@app.command()
def main(
    migrations_dir: str = typer.Option(
        "supabase/migrations",
        help="Directory containing ordered *.sql migrations.",
    ),
    strict_checksum: bool = typer.Option(
        True,
        help="Fail if an applied migration has a checksum mismatch.",
    ),
) -> None:
    settings = get_settings()
    conn = get_connection(settings)
    try:
        execute_query(
            conn,
            """
            CREATE TABLE IF NOT EXISTS schema_migrations_startuplens (
              version text PRIMARY KEY,
              checksum text NOT NULL,
              applied_at timestamptz NOT NULL DEFAULT now()
            )
            """,
        )

        applied_rows = execute_query(
            conn,
            """
            SELECT version, checksum
            FROM schema_migrations_startuplens
            ORDER BY version
            """,
        )
        applied = {r["version"]: r["checksum"] for r in applied_rows}

        files = sorted(Path(migrations_dir).glob("*.sql"))
        if not files:
            logger.warning("no_migrations_found", migrations_dir=migrations_dir)
            return

        applied_count = 0
        skipped_count = 0
        for path in files:
            version = path.stem
            sql_text = path.read_text(encoding="utf-8")
            checksum = _checksum(sql_text)
            existing = applied.get(version)
            if existing:
                if strict_checksum and existing != checksum:
                    raise typer.BadParameter(
                        f"Checksum mismatch for {version}: expected {existing}, got {checksum}",
                    )
                skipped_count += 1
                continue

            with conn.cursor() as cur:
                cur.execute("SAVEPOINT startuplens_migration_sp")
                try:
                    cur.execute(sql_text)
                except (DuplicateTable, DuplicateObject, DuplicateColumn):
                    cur.execute("ROLLBACK TO SAVEPOINT startuplens_migration_sp")
                    logger.warning("migration_duplicate_object_skipped", version=version)
                except Exception:
                    cur.execute("ROLLBACK TO SAVEPOINT startuplens_migration_sp")
                    raise
                finally:
                    cur.execute("RELEASE SAVEPOINT startuplens_migration_sp")
            execute_query(
                conn,
                """
                INSERT INTO schema_migrations_startuplens(version, checksum)
                VALUES (%s, %s)
                """,
                (version, checksum),
            )
            applied_count += 1
            logger.info("migration_applied", version=version)
            conn.commit()
        logger.info(
            "migration_run_complete",
            applied=applied_count,
            skipped=skipped_count,
            total=len(files),
        )
    finally:
        conn.close()


if __name__ == "__main__":
    app()
