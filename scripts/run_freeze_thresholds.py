#!/usr/bin/env python3
"""Freeze threshold-source file hashes for governance and change control."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import structlog
import typer

from startuplens.config import get_settings
from startuplens.db import execute_query, get_connection

logger = structlog.get_logger(__name__)
app = typer.Typer()

THRESHOLD_FILES = [
    "src/startuplens/backtest/metrics.py",
    "web/src/lib/scoring/gates.ts",
    "web/src/lib/scoring/recommendation.ts",
]


def quarter_label_for(d: date) -> str:
    q = ((d.month - 1) // 3) + 1
    return f"{d.year}-Q{q}"


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


@app.command()
def main(
    quarter_label: str | None = typer.Option(
        None,
        help="Quarter label (e.g., 2026-Q1). Defaults to current quarter.",
    ),
    note: str = typer.Option(
        "Threshold freeze for governed scoring/backtest parameters.",
        help="Reason recorded with the freeze.",
    ),
    frozen_by: str = typer.Option("owner", help="Actor name/email for audit trail."),
    deactivate_previous: bool = typer.Option(
        True,
        help="Deactivate previously active freezes before inserting this one.",
    ),
) -> None:
    label = quarter_label or quarter_label_for(date.today())

    files_payload: dict[str, str] = {}
    for rel in THRESHOLD_FILES:
        p = Path(rel)
        if not p.exists():
            raise typer.BadParameter(f"Threshold source missing: {rel}")
        files_payload[rel] = file_hash(p)

    settings = get_settings()
    conn = get_connection(settings)
    try:
        if deactivate_previous:
            execute_query(conn, "UPDATE threshold_freezes SET active = false WHERE active = true")

        inserted = execute_query(
            conn,
            """
            INSERT INTO threshold_freezes (
                quarter_label,
                freeze_note,
                files,
                frozen_by,
                active,
                created_at
            )
            VALUES (%s, %s, %s::jsonb, %s, true, now())
            RETURNING id::text, quarter_label, created_at::text
            """,
            (label, note, json.dumps(files_payload), frozen_by),
        )
        conn.commit()

        row = inserted[0]
        logger.info(
            "threshold_freeze_created",
            freeze_id=row["id"],
            quarter_label=row["quarter_label"],
            created_at=row["created_at"],
            file_count=len(files_payload),
        )
    finally:
        conn.close()


if __name__ == "__main__":
    app()
