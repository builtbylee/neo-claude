"""Companies House monthly bulk snapshot ingestion.

Ingests the free Companies House data product as raw records (not canonical truth)
for provenance, reconciliation support, and status backfill.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from startuplens.db import execute_many, execute_query

if TYPE_CHECKING:
    import psycopg

logger = structlog.get_logger(__name__)


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path, "r") as zf:
            candidates = [n for n in zf.namelist() if n.lower().endswith((".csv", ".txt"))]
            if not candidates:
                return []
            data = zf.read(candidates[0])
            text = data.decode("utf-8", errors="replace")
    else:
        text = path.read_text(encoding="utf-8", errors="replace")

    sample = text[:2048]
    delimiter = "\t" if sample.count("\t") > sample.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    out: list[dict[str, Any]] = []
    for row in reader:
        normalized = {
            str(k).strip().lower(): (v.strip() if isinstance(v, str) else v)
            for k, v in row.items()
            if k
        }
        out.append(normalized)
    return out


def _pick(row: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = row.get(key)
        if value:
            return str(value).strip()
    return None


def ingest_companies_house_snapshot(
    conn: psycopg.Connection,
    snapshot_path: Path,
    *,
    limit: int | None = None,
) -> dict[str, int]:
    rows = _read_rows(snapshot_path)
    if limit is not None:
        rows = rows[:limit]

    updates: list[tuple[Any, ...]] = []
    raw_rows: list[tuple[Any, ...]] = []

    for row in rows:
        company_number = _pick(row, "company_number", "companynumber", "company number")
        if not company_number:
            continue
        company_name = _pick(row, "company_name", "companyname", "company name") or "Unknown"
        status = _pick(row, "company_status", "status")
        sic = _pick(row, "sic_code", "sic", "siccode")

        updates.append(
            (
                company_name,
                "UK",
                sic,
                sic,
                "companies_house",
                company_number,
                status,
            ),
        )

        raw_rows.append(
            (
                "companies_house_bulk_snapshot",
                company_number,
                json.dumps(row),
            ),
        )

    if updates:
        execute_many(
            conn,
            """
            INSERT INTO companies (
              name, country, sector, sic_code, source, source_id, current_status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source, source_id) WHERE source_id IS NOT NULL DO UPDATE
            SET
              name = EXCLUDED.name,
              sector = COALESCE(EXCLUDED.sector, companies.sector),
              current_status = COALESCE(EXCLUDED.current_status, companies.current_status)
            """,
            updates,
        )

    # Build company_id mapping for raw provenance insert.
    source_ids = [r[1] for r in raw_rows]
    company_lookup: dict[str, tuple[str, str | None]] = {}
    if source_ids:
        mapped = execute_query(
            conn,
            """
            SELECT source_id, id, entity_id
            FROM companies
            WHERE source = 'companies_house'
              AND source_id = ANY(%s)
            """,
            (source_ids,),
        )
        company_lookup = {str(r["source_id"]): (str(r["id"]), r.get("entity_id")) for r in mapped}

    provenance_rows: list[tuple[Any, ...]] = []
    for source_name, source_record_id, payload in raw_rows:
        mapped = company_lookup.get(source_record_id)
        if not mapped:
            continue
        provenance_rows.append(
            (
                mapped[0],
                mapped[1],
                source_name,
                source_record_id,
                payload,
            ),
        )

    if provenance_rows:
        execute_many(
            conn,
            """
            INSERT INTO company_source_raw (
              company_id,
              entity_id,
              source_name,
              source_record_id,
              source_timestamp,
              source_tier,
              raw_payload
            ) VALUES (%s, %s, %s, %s, now(), 'A', %s::jsonb)
            ON CONFLICT (source_name, source_record_id) DO UPDATE
            SET
              company_id = EXCLUDED.company_id,
              entity_id = COALESCE(EXCLUDED.entity_id, company_source_raw.entity_id),
              source_timestamp = now(),
              source_tier = 'A',
              raw_payload = EXCLUDED.raw_payload
            """,
            provenance_rows,
        )

    conn.commit()
    return {
        "rows_read": len(rows),
        "companies_upserted": len(updates),
        "raw_records_upserted": len(provenance_rows),
    }
