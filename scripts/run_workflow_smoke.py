#!/usr/bin/env python3
"""Exercise end-to-end analyst workflow against deployed APIs."""

from __future__ import annotations

import uuid

import httpx
import structlog
import typer

logger = structlog.get_logger(__name__)
app = typer.Typer()


def _expect(resp: httpx.Response, status: int, label: str) -> dict:
    if resp.status_code != status:
        body = resp.text[:300].replace("\n", " ")
        raise RuntimeError(f"{label} failed: status={resp.status_code}, body={body}")
    return resp.json() if resp.text else {}


@app.command()
def main(
    base_url: str = typer.Option(
        "https://startuplens.lee-sam78.workers.dev",
        help="Deployment URL to verify.",
    ),
    user_email: str = typer.Option("owner@example.com", help="Header email for actor context."),
) -> None:
    headers = {"x-user-email": user_email, "Content-Type": "application/json"}
    deal_name = f"Smoke Deal {uuid.uuid4().hex[:8]}"

    with httpx.Client(follow_redirects=True, timeout=40) as client:
        created = _expect(
            client.post(
                f"{base_url.rstrip('/')}/api/deals",
                headers=headers,
                json={
                    "companyName": deal_name,
                    "status": "screening",
                    "priority": "high",
                    "ownerEmail": user_email,
                    "recommendationClass": "deep_diligence",
                    "convictionScore": 62,
                },
            ),
            200,
            "create deal",
        )
        deal_id = created.get("deal", {}).get("id")
        if not deal_id:
            raise RuntimeError("create deal returned no deal id")
        logger.info("workflow_deal_created", deal_id=deal_id)

        _expect(
            client.patch(
                f"{base_url.rstrip('/')}/api/deals/{deal_id}",
                headers=headers,
                json={"status": "diligence", "ownerEmail": user_email},
            ),
            200,
            "update deal status",
        )

        _expect(
            client.post(
                f"{base_url.rstrip('/')}/api/deals/{deal_id}/tasks",
                headers=headers,
                json={
                    "title": "Verify revenue evidence",
                    "assigneeEmail": user_email,
                    "evidenceRequired": True,
                },
            ),
            200,
            "create diligence task",
        )

        _expect(
            client.post(
                f"{base_url.rstrip('/')}/api/deals/{deal_id}/comments",
                headers=headers,
                json={"body": "Smoke test note", "authorEmail": user_email},
            ),
            200,
            "create comment",
        )

        _expect(
            client.post(
                f"{base_url.rstrip('/')}/api/deals/{deal_id}/approve",
                headers=headers,
                json={
                    "requestedBy": user_email,
                    "approverEmail": user_email,
                    "status": "approved",
                    "decisionReason": "Smoke test approval",
                },
            ),
            200,
            "approve deal",
        )

        _expect(
            client.get(
                f"{base_url.rstrip('/')}/api/deals/{deal_id}/activity",
                headers={"x-user-email": user_email},
            ),
            200,
            "read activity",
        )

    logger.info("workflow_smoke_passed", deal_id=deal_id)


if __name__ == "__main__":
    app()
