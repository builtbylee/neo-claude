#!/usr/bin/env python3
"""Run post-deploy API smoke checks."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
import structlog
import typer

logger = structlog.get_logger(__name__)
app = typer.Typer()


@dataclass
class SmokeCheck:
    name: str
    method: str
    path: str
    expected_status: int
    json_body: dict | None = None


def _request(
    client: httpx.Client,
    base_url: str,
    check: SmokeCheck,
    user_email: str,
) -> tuple[bool, str]:
    url = f"{base_url.rstrip('/')}{check.path}"
    headers = {"x-user-email": user_email}
    if check.method == "GET":
        resp = client.get(url, headers=headers, timeout=30)
    else:
        resp = client.post(
            url,
            headers={**headers, "Content-Type": "application/json"},
            json=check.json_body or {},
            timeout=30,
        )
    ok = resp.status_code == check.expected_status
    msg = f"{check.name}: {resp.status_code} ({url})"
    if not ok:
        snippet = resp.text[:220].replace("\n", " ")
        msg = f"{msg} body={snippet}"
    return ok, msg


@app.command()
def main(
    base_url: str = typer.Option(
        "https://startuplens.lee-sam78.workers.dev",
        help="Deployment URL to verify.",
    ),
    user_email: str = typer.Option(
        "owner@example.com",
        help="User email header for non-auth fallback contexts.",
    ),
) -> None:
    checks = [
        SmokeCheck("health", "GET", "/api/health", 200),
        SmokeCheck("deals", "GET", "/api/deals", 200),
        SmokeCheck("portfolio_context", "GET", "/api/portfolio/context", 200),
        SmokeCheck("model_health", "GET", "/api/model-health", 200),
        SmokeCheck(
            "quick_score",
            "POST",
            "/api/score/quick",
            200,
            {
                "companyName": "Monzo",
                "websiteUrl": "https://monzo.com",
                "sector": "banking and financial services",
            },
        ),
        SmokeCheck(
            "batch_score",
            "POST",
            "/api/score/batch",
            200,
            {"deals": [{"companyName": "Monzo"}]},
        ),
    ]

    failures = []
    with httpx.Client(follow_redirects=True) as client:
        for check in checks:
            ok, msg = _request(client, base_url, check, user_email)
            if ok:
                logger.info("smoke_check_passed", check=check.name)
            else:
                logger.error("smoke_check_failed", check=check.name, message=msg)
                failures.append(msg)

    if failures:
        logger.error("smoke_checks_failed", failures=failures)
        raise typer.Exit(code=2)

    logger.info("smoke_checks_passed", total=len(checks))


if __name__ == "__main__":
    app()
