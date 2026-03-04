#!/usr/bin/env python3
"""Reconcile transaction field facts and apply analyst valuation gate."""

from __future__ import annotations

import structlog
import typer

from startuplens.config import get_settings
from startuplens.db import get_connection
from startuplens.pipelines.transaction_truth import (
    apply_valuation_truth_gate,
    reconcile_transaction_round_fields,
)

logger = structlog.get_logger(__name__)
app = typer.Typer()


@app.command()
def main(
    limit_rounds: int | None = typer.Option(None, help="Optional max rounds to reconcile."),
) -> None:
    settings = get_settings()
    conn = get_connection(settings)
    try:
        reconciled = reconcile_transaction_round_fields(conn, limit_rounds=limit_rounds)
        gated = apply_valuation_truth_gate(conn)
        logger.info("transaction_reconciliation_complete", reconciled=reconciled, gated=gated)
    finally:
        conn.close()


if __name__ == "__main__":
    app()
