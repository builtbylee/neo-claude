"""Airtable CRM sync helpers.

Uses Airtable free tier as the default lightweight CRM backend.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from startuplens.config import Settings

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class AirtableConfig:
    api_key: str
    base_id: str
    table_name: str

    @property
    def endpoint(self) -> str:
        return f"https://api.airtable.com/v0/{self.base_id}/{self.table_name}"


def get_airtable_config(settings: Settings) -> AirtableConfig | None:
    if not settings.airtable_api_key or not settings.airtable_base_id:
        return None
    return AirtableConfig(
        api_key=settings.airtable_api_key,
        base_id=settings.airtable_base_id,
        table_name=settings.airtable_table_name,
    )


def _headers(cfg: AirtableConfig) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {cfg.api_key}",
        "Content-Type": "application/json",
    }


def list_records(cfg: AirtableConfig, max_records: int = 100) -> list[dict[str, Any]]:
    with httpx.Client(timeout=20.0) as client:
        resp = client.get(
            cfg.endpoint,
            headers=_headers(cfg),
            params={"maxRecords": max_records},
        )
        resp.raise_for_status()
        payload = resp.json()
    return payload.get("records", [])


def upsert_records(
    cfg: AirtableConfig,
    records: list[dict[str, Any]],
    key_field: str = "DealID",
) -> dict[str, int]:
    """Upsert records in batches, matching on key_field."""
    if not records:
        return {"upserted": 0, "failed": 0}

    upserted = 0
    failed = 0
    with httpx.Client(timeout=20.0) as client:
        for i in range(0, len(records), 10):
            chunk = records[i : i + 10]
            try:
                resp = client.patch(
                    cfg.endpoint,
                    headers=_headers(cfg),
                    json={
                        "performUpsert": {"fieldsToMergeOn": [key_field]},
                        "records": chunk,
                        "typecast": True,
                    },
                )
                resp.raise_for_status()
                upserted += len(chunk)
            except httpx.HTTPError:
                failed += len(chunk)
                logger.warning("airtable_upsert_failed", chunk_size=len(chunk))
    return {"upserted": upserted, "failed": failed}

