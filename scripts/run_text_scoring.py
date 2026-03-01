#!/usr/bin/env python3
"""CLI for the Claude text scoring pipeline.

Subcommands:
    scrape   — Scrape Form C narrative text from EDGAR
    score    — Score scraped texts with Claude (7 dimensions)
    auc      — Compute AUC of text scores vs outcomes
    run-all  — Run scrape → score → auc end-to-end
"""

from __future__ import annotations

import structlog
import typer

logger = structlog.get_logger(__name__)

app = typer.Typer(help="Claude text scoring pipeline for Form C filings.")


@app.command()
def scrape(
    limit: int | None = typer.Option(None, help="Max companies to scrape"),
) -> None:
    """Scrape Form C narrative text from EDGAR filings."""
    from startuplens.pipelines.sec_edgar_text import run_text_scraper

    count = run_text_scraper(limit=limit)
    typer.echo(f"Scraped {count} filing texts.")


@app.command()
def score(
    limit: int | None = typer.Option(None, help="Max texts to score"),
) -> None:
    """Score scraped texts with Claude (7 dimensions + aggregate)."""
    from startuplens.scoring.claude_text_scorer import run_text_scorer

    count = run_text_scorer(limit=limit)
    typer.echo(f"Scored {count} texts.")


@app.command()
def auc() -> None:
    """Compute AUC of text_quality_score vs crowdfunding outcomes."""
    from startuplens.backtest.text_score_auc import (
        compute_claude_text_auc,
        compute_dimension_aucs,
    )
    from startuplens.db import get_connection

    conn = get_connection()
    try:
        overall = compute_claude_text_auc(conn)
        typer.echo(f"\nOverall text_quality_score AUC: {overall:.4f}")

        if overall > 0:
            typer.echo(f"  Threshold: 0.60  {'PASS' if overall >= 0.60 else 'FAIL'}")

            dim_aucs = compute_dimension_aucs(conn)
            typer.echo("\nPer-dimension AUCs:")
            for dim, val in sorted(dim_aucs.items(), key=lambda x: -x[1]):
                typer.echo(f"  {dim:30s} {val:.4f}")
        else:
            typer.echo("  Insufficient data for AUC computation.")
    finally:
        conn.close()


@app.command(name="run-all")
def run_all(
    limit: int | None = typer.Option(None, help="Max companies per stage"),
) -> None:
    """Run scrape → score → auc end-to-end."""
    typer.echo("=== Stage 1: Scraping EDGAR texts ===")
    scrape(limit=limit)

    typer.echo("\n=== Stage 2: Scoring with Claude ===")
    score(limit=limit)

    typer.echo("\n=== Stage 3: Computing AUC ===")
    auc()


if __name__ == "__main__":
    app()
