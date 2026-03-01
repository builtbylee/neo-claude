"""Claude-based text quality scoring for Form C filings.

Scores 7 dimensions of text quality (0-100 each) plus an aggregate
text_quality_score. Uses Claude Sonnet with structured JSON output.
Results stored in claude_text_scores table.

Dimension definitions per ARCHITECTURE.md:
  clarity, claims_plausibility, problem_specificity, differentiation_depth,
  founder_domain_signal, risk_honesty, business_model_clarity
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import TYPE_CHECKING, Any

import anthropic
import structlog

if TYPE_CHECKING:
    import psycopg

    from startuplens.config import Settings

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are evaluating the quality and credibility of a startup's offering text \
from an SEC Form C filing. Score each dimension 0-100 based on the text provided.

Calibration guidance:
- Most offerings score 40-60 on each dimension.
- Scores above 75 indicate genuinely exceptional quality for that dimension.
- Scores below 30 indicate serious concerns.
- Be skeptical: crowdfunding offerings have an 8.5x higher failure rate \
than matched non-crowdfunding companies.

Return ONLY valid JSON with no additional text or markdown formatting."""

_USER_TEMPLATE = """\
OFFERING TEXT:
{narrative_text}

COMPANY CONTEXT:
{context_json}

Score these dimensions (0-100 each):
1. CLARITY: Is the value proposition clear and specific? Or vague and buzzword-heavy?
2. CLAIMS_PLAUSIBILITY: Are market size claims, growth projections, and competitive \
claims believable? Or inflated and unsupported?
3. PROBLEM_SPECIFICITY: Is the problem well-defined with evidence of real customer \
pain? Or generic and assumed?
4. DIFFERENTIATION_DEPTH: Is the competitive advantage specific, defensible, and \
hard to replicate? Or superficial?
5. FOUNDER_DOMAIN_SIGNAL: Does the language demonstrate deep domain expertise? \
Or generic business-speak?
6. RISK_HONESTY: Does the text acknowledge real risks and challenges? \
Or is it unrealistically optimistic?
7. BUSINESS_MODEL_CLARITY: Is how the company makes money clear and logical? \
Or vague?

Return JSON:
{{
  "clarity": <int 0-100>,
  "claims_plausibility": <int 0-100>,
  "problem_specificity": <int 0-100>,
  "differentiation_depth": <int 0-100>,
  "founder_domain_signal": <int 0-100>,
  "risk_honesty": <int 0-100>,
  "business_model_clarity": <int 0-100>,
  "text_quality_score": <int 0-100>,
  "red_flags": [<string>, ...],
  "reasoning": "<2-3 sentences>"
}}"""

# Version hash of the prompt templates for tracking drift
PROMPT_VERSION = hashlib.sha256(
    (_SYSTEM_PROMPT + _USER_TEMPLATE).encode()
).hexdigest()[:12]

MODEL_ID = "claude-sonnet-4-5-20250514"

_SCORE_DIMENSIONS = (
    "clarity",
    "claims_plausibility",
    "problem_specificity",
    "differentiation_depth",
    "founder_domain_signal",
    "risk_honesty",
    "business_model_clarity",
    "text_quality_score",
)

# Max text to send to Claude (tokens are roughly 4 chars)
_MAX_TEXT_CHARS = 30_000


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def build_scoring_prompt(
    narrative_text: str,
    context: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Build the Claude messages list for text scoring."""
    # Truncate very long texts
    text = narrative_text[:_MAX_TEXT_CHARS]
    if len(narrative_text) > _MAX_TEXT_CHARS:
        text += "\n\n[Text truncated for length]"

    context_json = json.dumps(context or {}, indent=2, default=str)

    return [
        {"role": "user", "content": _USER_TEMPLATE.format(
            narrative_text=text,
            context_json=context_json,
        )},
    ]


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def score_text(
    client: anthropic.Anthropic,
    narrative_text: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Score a single piece of text using Claude. Returns parsed scores or None."""
    messages = build_scoring_prompt(narrative_text, context)

    try:
        response = client.messages.create(
            model=MODEL_ID,
            max_tokens=1024,
            system=_SYSTEM_PROMPT,
            messages=messages,
        )
    except anthropic.APIError as exc:
        logger.error("claude_api_error", error=str(exc))
        return None

    # Extract JSON from response
    raw_text = response.content[0].text.strip()

    # Strip markdown code fences if present
    if raw_text.startswith("```"):
        raw_text = raw_text.split("\n", 1)[-1]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3].strip()

    try:
        scores = json.loads(raw_text)
    except json.JSONDecodeError:
        logger.error("claude_json_parse_error", raw=raw_text[:500])
        return None

    # Validate all dimension scores are present and in range
    for dim in _SCORE_DIMENSIONS:
        val = scores.get(dim)
        if not isinstance(val, int | float) or not (0 <= val <= 100):
            logger.error("claude_invalid_score", dimension=dim, value=val)
            return None
        scores[dim] = int(val)

    scores["_raw_response"] = raw_text
    return scores


# ---------------------------------------------------------------------------
# Batch scoring pipeline
# ---------------------------------------------------------------------------


def _get_texts_to_score(
    conn: psycopg.Connection,
    prompt_version: str,
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Get form_c_texts that haven't been scored yet."""
    from startuplens.db import execute_query

    query = """
        SELECT
            t.id AS form_c_text_id,
            t.company_id,
            t.narrative_text,
            c.name AS company_name,
            c.sector,
            co.amount_raised,
            co.funding_target,
            co.had_revenue,
            co.revenue_at_raise,
            co.founder_count,
            co.company_age_at_raise_months
        FROM sec_form_c_texts t
        JOIN companies c ON c.id = t.company_id
        LEFT JOIN crowdfunding_outcomes co ON co.company_id = c.id
        LEFT JOIN claude_text_scores s
            ON s.company_id = t.company_id AND s.prompt_version = %s
        WHERE s.id IS NULL
        ORDER BY t.created_at ASC
    """
    if limit:
        query += f"\n        LIMIT {int(limit)}"

    return execute_query(conn, query, (prompt_version,))


def score_batch(
    conn: psycopg.Connection,
    settings: Settings,
    *,
    limit: int | None = None,
) -> int:
    """Score unscored texts using Claude. Returns count of texts scored."""
    if not settings.anthropic_api_key:
        logger.error("no_anthropic_api_key", hint="Set SL_ANTHROPIC_API_KEY in .env")
        return 0

    texts = _get_texts_to_score(conn, PROMPT_VERSION, limit=limit)
    logger.info("score_targets", count=len(texts))

    if not texts:
        return 0

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    scored = 0

    for i, row in enumerate(texts):
        company_name = row["company_name"]
        logger.info(
            "scoring_text",
            company=company_name,
            progress=f"{i + 1}/{len(texts)}",
        )

        # Build context from structured data
        context = {
            "company_name": company_name,
            "sector": row.get("sector"),
            "funding_target": row.get("funding_target"),
            "amount_raised": row.get("amount_raised"),
            "had_revenue": row.get("had_revenue"),
            "revenue_at_raise": row.get("revenue_at_raise"),
            "founder_count": row.get("founder_count"),
            "company_age_months": row.get("company_age_at_raise_months"),
        }

        scores = score_text(client, row["narrative_text"], context)

        if scores:
            _store_scores(conn, row["company_id"], row["form_c_text_id"], scores)
            scored += 1
            logger.info(
                "text_scored",
                company=company_name,
                quality=scores["text_quality_score"],
            )

        # Brief pause between API calls
        time.sleep(0.5)

    logger.info("scoring_complete", scored=scored, total=len(texts))
    return scored


def _store_scores(
    conn: psycopg.Connection,
    company_id: str,
    form_c_text_id: str,
    scores: dict[str, Any],
) -> None:
    """Insert Claude text scores into the database."""
    raw_response = scores.pop("_raw_response", None)

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO claude_text_scores (
                company_id, form_c_text_id,
                clarity, claims_plausibility, problem_specificity,
                differentiation_depth, founder_domain_signal,
                risk_honesty, business_model_clarity, text_quality_score,
                red_flags, reasoning, prompt_version, model_id, raw_response
            ) VALUES (
                %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s
            )
            ON CONFLICT (company_id, prompt_version) DO NOTHING
            """,
            (
                company_id,
                form_c_text_id,
                scores["clarity"],
                scores["claims_plausibility"],
                scores["problem_specificity"],
                scores["differentiation_depth"],
                scores["founder_domain_signal"],
                scores["risk_honesty"],
                scores["business_model_clarity"],
                scores["text_quality_score"],
                json.dumps(scores.get("red_flags", [])),
                scores.get("reasoning"),
                PROMPT_VERSION,
                MODEL_ID,
                json.dumps(raw_response) if raw_response else None,
            ),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def run_text_scorer(*, limit: int | None = None) -> int:
    """Run the text scoring pipeline. Returns count of texts scored."""
    from startuplens.config import get_settings
    from startuplens.db import get_connection

    settings = get_settings()
    conn = get_connection(settings)
    try:
        return score_batch(conn, settings, limit=limit)
    finally:
        conn.close()
