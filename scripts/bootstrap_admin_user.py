#!/usr/bin/env python3
"""Bootstrap or update an admin user role for production auth."""

from __future__ import annotations

import structlog
import typer

from startuplens.config import get_settings
from startuplens.db import execute_query, get_connection

logger = structlog.get_logger(__name__)
app = typer.Typer()


@app.command()
def main(
    email: str = typer.Option(..., help="Google-authenticated user email."),
    can_approve: bool = typer.Option(True, help="Grant approval authority."),
    active: bool = typer.Option(True, help="Enable account."),
) -> None:
    normalized = email.strip().lower()
    if "@" not in normalized:
        raise typer.BadParameter("email must be valid")

    settings = get_settings()
    conn = get_connection(settings)
    try:
        execute_query(
            conn,
            """
            INSERT INTO user_roles (email, role, can_approve, active, updated_at)
            VALUES (%s, 'admin', %s, %s, now())
            ON CONFLICT (email)
            DO UPDATE
              SET role = 'admin',
                  can_approve = EXCLUDED.can_approve,
                  active = EXCLUDED.active,
                  updated_at = now()
            """,
            (normalized, can_approve, active),
        )
        conn.commit()
        logger.info(
            "admin_user_bootstrapped",
            email=normalized,
            can_approve=can_approve,
            active=active,
        )
    finally:
        conn.close()


if __name__ == "__main__":
    app()
