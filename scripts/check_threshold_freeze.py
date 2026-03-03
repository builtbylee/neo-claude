#!/usr/bin/env python3
"""Enforce threshold freeze: fail when governed threshold files diverge from active freeze."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import structlog
import typer

from startuplens.config import get_settings
from startuplens.db import execute_query, get_connection

logger = structlog.get_logger(__name__)
app = typer.Typer()



def sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


@app.command()
def main() -> None:
    settings = get_settings()
    if not settings.database_url:
        logger.warning("threshold_freeze_check_skipped", reason="missing_database_url")
        raise typer.Exit(code=0)

    conn = get_connection(settings)
    try:
        rows = execute_query(
            conn,
            """
            SELECT id::text, quarter_label, files
            FROM threshold_freezes
            WHERE active = true
            ORDER BY created_at DESC
            LIMIT 1
            """,
        )
    finally:
        conn.close()

    if not rows:
        logger.warning("threshold_freeze_check_skipped", reason="no_active_freeze")
        raise typer.Exit(code=0)

    row = rows[0]
    expected = row["files"] or {}
    if isinstance(expected, str):
        expected = json.loads(expected)

    drifted: list[tuple[str, str, str]] = []
    missing: list[str] = []

    for rel, expected_hash in expected.items():
        p = Path(rel)
        if not p.exists():
            missing.append(rel)
            continue
        current_hash = sha256(p)
        if current_hash != expected_hash:
            drifted.append((rel, expected_hash, current_hash))

    if missing or drifted:
        logger.error(
            "threshold_freeze_check_failed",
            freeze_id=row["id"],
            quarter_label=row["quarter_label"],
            missing_files=missing,
            drifted_files=[d[0] for d in drifted],
        )
        raise typer.Exit(code=1)

    logger.info(
        "threshold_freeze_check_passed",
        freeze_id=row["id"],
        quarter_label=row["quarter_label"],
        files=len(expected),
    )


if __name__ == "__main__":
    app()
