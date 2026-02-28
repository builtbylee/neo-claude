"""Deterministic (exact-match) entity resolution stage.

Provides name normalisation, source-ID lookup, legal-name lookup,
and canonical entity creation/linking helpers.  All database interaction
uses raw SQL via psycopg3.
"""

from __future__ import annotations

import re
import uuid

import psycopg
from unidecode import unidecode

from startuplens.db import execute_query

# ---------------------------------------------------------------------------
# Suffix patterns to strip (order matters — match longest first)
# ---------------------------------------------------------------------------

_SUFFIX_PATTERN = re.compile(
    r"\b("
    r"limited|ltd|inc|incorporated|llc|l\.l\.c\.|plc|p\.l\.c\.|"
    r"corp|corporation|co|company|gmbh|ag|sa|sas|sarl|pty|"
    r"pvt|private"
    r")\.?\s*$",
    re.IGNORECASE,
)


def normalize_name(name: str) -> str:
    """Normalise a company name for deterministic matching.

    Steps:
      1. Transliterate Unicode to ASCII (e.g. ü → u).
      2. Lowercase.
      3. Strip common legal suffixes (Ltd, Inc, LLC, etc.).
      4. Remove non-alphanumeric characters (keep spaces).
      5. Collapse whitespace and strip leading/trailing spaces.
    """
    # Transliterate
    text = unidecode(name)

    # Lowercase
    text = text.lower()

    # Strip legal suffixes (may need multiple passes for e.g. "Foo Ltd.")
    text = _SUFFIX_PATTERN.sub("", text).strip()

    # Remove non-alphanumeric except spaces
    text = re.sub(r"[^a-z0-9\s]", "", text)

    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()

    return text


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

def match_by_source_id(
    conn: psycopg.Connection,
    source: str,
    source_identifier: str,
) -> str | None:
    """Look up an entity_id by exact (source, source_identifier) pair.

    Returns the entity_id as a string, or ``None`` if no link exists.
    """
    rows = execute_query(
        conn,
        """
        SELECT entity_id::text
        FROM entity_links
        WHERE source = %s AND source_identifier = %s
        LIMIT 1
        """,
        (source, source_identifier),
    )
    if rows:
        return rows[0]["entity_id"]
    return None


def match_by_legal_name(
    conn: psycopg.Connection,
    name: str,
    country: str,
) -> str | None:
    """Look up an entity_id by normalised legal name + country.

    Normalises *name* before comparing against stored ``primary_name``
    values (also normalised at insertion time).  Returns ``None`` when
    no match is found.
    """
    norm = normalize_name(name)
    rows = execute_query(
        conn,
        """
        SELECT id::text AS entity_id
        FROM canonical_entities
        WHERE lower(primary_name) = %s AND lower(country) = %s
        LIMIT 1
        """,
        (norm, country.lower()),
    )
    if rows:
        return rows[0]["entity_id"]
    return None


# ---------------------------------------------------------------------------
# Creation helpers
# ---------------------------------------------------------------------------

def create_canonical_entity(
    conn: psycopg.Connection,
    name: str,
    country: str,
) -> str:
    """Insert a new canonical entity and return its UUID as a string.

    The *primary_name* is stored in normalised form so that future
    ``match_by_legal_name`` look-ups work without re-normalising on
    every query.
    """
    entity_id = str(uuid.uuid4())
    norm = normalize_name(name)
    execute_query(
        conn,
        """
        INSERT INTO canonical_entities (id, primary_name, country)
        VALUES (%s, %s, %s)
        """,
        (entity_id, norm, country.lower()),
    )
    return entity_id


def link_entity(
    conn: psycopg.Connection,
    entity_id: str,
    source: str,
    source_identifier: str,
    confidence: int,
    *,
    match_method: str = "deterministic",
    source_name: str | None = None,
) -> None:
    """Create an entity_link tying a source record to a canonical entity.

    Parameters
    ----------
    entity_id:
        UUID of the canonical entity.
    source:
        Source system identifier (e.g. ``sec_edgar``, ``companies_house``).
    source_identifier:
        The external ID within *source* (CIK, company number, etc.).
    confidence:
        Match confidence 0-100.
    match_method:
        How the link was established (``exact_id``, ``deterministic``,
        ``probabilistic``).
    source_name:
        Name as it appears in the source system (optional).
    """
    link_id = str(uuid.uuid4())
    execute_query(
        conn,
        """
        INSERT INTO entity_links
            (id, entity_id, source, source_identifier, source_name,
             match_method, confidence)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (link_id, entity_id, source, source_identifier, source_name, match_method, confidence),
    )
