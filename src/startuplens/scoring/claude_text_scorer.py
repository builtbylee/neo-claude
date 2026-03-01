"""Claude-based investment quality scoring for Form C companies.

Scores 7 dimensions of quality (0-100 each) plus an aggregate
text_quality_score. Uses Claude Haiku with batched scoring (10 companies
per API call) for efficiency. Results stored in claude_text_scores table.

Dimension definitions:
  clarity, claims_plausibility, problem_specificity, differentiation_depth,
  founder_domain_signal, risk_honesty, business_model_clarity
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
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
You are evaluating the investment quality of startups based on structured \
financial profiles from SEC Reg CF filings. Score each dimension 0-100.

Calibration guidance:
- Most crowdfunding companies score 30-50 on each dimension.
- Scores above 70 indicate genuinely strong indicators.
- Scores below 20 indicate serious concerns or missing data.
- ~85% of equity crowdfunding companies fail. Be calibrated accordingly.

Return ONLY valid JSON with no additional text or markdown formatting."""

_BATCH_TEMPLATE = """\
Score each company below on 7 dimensions (0-100 each) plus an aggregate \
text_quality_score (0-100). Dimensions:
1. CLARITY: Business model focus (name, sector, financials)
2. CLAIMS_PLAUSIBILITY: Financial trajectory plausibility
3. PROBLEM_SPECIFICITY: Evidence of real market need
4. DIFFERENTIATION_DEPTH: Competitive moat signals
5. FOUNDER_DOMAIN_SIGNAL: Execution capability indicators
6. RISK_HONESTY: Financial risk severity (inverse: lower = more risky)
7. BUSINESS_MODEL_CLARITY: Revenue pattern coherence

{profiles}

Return a JSON array with one object per company, in order:
[{{"company": "<name>", "clarity": N, "claims_plausibility": N, \
"problem_specificity": N, "differentiation_depth": N, \
"founder_domain_signal": N, "risk_honesty": N, \
"business_model_clarity": N, "text_quality_score": N}}]"""

# Version hash of the prompt templates for tracking drift
PROMPT_VERSION = hashlib.sha256(
    (_SYSTEM_PROMPT + _BATCH_TEMPLATE).encode()
).hexdigest()[:12]

MODEL_ID = "claude-haiku-4-5-20251001"

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

BATCH_SIZE = 10


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
        SELECT DISTINCT ON (t.company_id)
            t.id AS form_c_text_id,
            t.company_id,
            t.narrative_text,
            c.name AS company_name
        FROM sec_form_c_texts t
        JOIN companies c ON c.id = t.company_id
        LEFT JOIN claude_text_scores s
            ON s.company_id = t.company_id AND s.prompt_version = %s
        WHERE s.id IS NULL
        ORDER BY t.company_id, t.created_at ASC
    """
    if limit:
        query += f"\n        LIMIT {int(limit)}"

    return execute_query(conn, query, (prompt_version,))


def score_batch(
    conn: psycopg.Connection,
    settings: Settings,
    *,
    limit: int | None = None,
    max_concurrent: int = 5,
) -> int:
    """Score unscored texts using Claude with batched async calls.

    Groups companies into batches of BATCH_SIZE and sends concurrent
    API calls with a semaphore to respect rate limits.
    """
    if not settings.anthropic_api_key:
        logger.error(
            "no_anthropic_api_key",
            hint="Set SL_ANTHROPIC_API_KEY in .env",
        )
        return 0

    texts = _get_texts_to_score(conn, PROMPT_VERSION, limit=limit)
    logger.info("score_targets", count=len(texts))

    if not texts:
        return 0

    # Group into batches
    batches = [
        texts[i : i + BATCH_SIZE]
        for i in range(0, len(texts), BATCH_SIZE)
    ]
    logger.info(
        "batch_plan",
        total_companies=len(texts),
        batches=len(batches),
        concurrent=max_concurrent,
    )

    scored = asyncio.run(
        _score_batches_async(conn, settings, batches, max_concurrent)
    )
    return scored


async def _score_batches_async(
    conn: psycopg.Connection,
    settings: Settings,
    batches: list[list[dict[str, Any]]],
    max_concurrent: int,
) -> int:
    """Score company batches concurrently using AsyncAnthropic."""
    client = anthropic.AsyncAnthropic(
        api_key=settings.anthropic_api_key,
        max_retries=5,
    )
    sem = asyncio.Semaphore(max_concurrent)
    scored = 0
    failed = 0
    total_batches = len(batches)

    async def _score_batch(
        batch: list[dict[str, Any]], batch_idx: int,
    ) -> int:
        async with sem:
            # Build profiles section
            profiles = []
            for i, row in enumerate(batch, 1):
                profiles.append(f"--- COMPANY {i} ---")
                profiles.append(row["narrative_text"])
            profiles_text = "\n\n".join(profiles)

            prompt = _BATCH_TEMPLATE.format(profiles=profiles_text)

            try:
                response = await client.messages.create(
                    model=MODEL_ID,
                    max_tokens=2048,
                    system=_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                )
            except anthropic.APIError as exc:
                logger.warning(
                    "batch_api_error",
                    batch=batch_idx,
                    error=str(exc)[:100],
                )
                return 0

            raw_text = response.content[0].text.strip()
            # Strip markdown fences
            if raw_text.startswith("```"):
                raw_text = raw_text.split("\n", 1)[-1]
                if raw_text.endswith("```"):
                    raw_text = raw_text[:-3].strip()

            # Find JSON array in response
            match = re.search(r"\[.*\]", raw_text, re.DOTALL)
            if not match:
                logger.warning(
                    "no_json_array", batch=batch_idx,
                    raw=raw_text[:200],
                )
                return 0

            try:
                results = json.loads(match.group())
            except json.JSONDecodeError:
                logger.warning("json_parse_error", batch=batch_idx)
                return 0

            batch_scored = 0
            for row, scores in zip(batch, results):
                valid = True
                for dim in _SCORE_DIMENSIONS:
                    val = scores.get(dim)
                    if not isinstance(val, int | float):
                        valid = False
                        break
                    val = int(val)
                    if not (0 <= val <= 100):
                        valid = False
                        break
                    scores[dim] = val

                if not valid:
                    continue

                _store_scores(
                    conn,
                    row["company_id"],
                    row["form_c_text_id"],
                    scores,
                )
                batch_scored += 1

            if (batch_idx + 1) % 50 == 0:
                logger.info(
                    "scoring_progress",
                    batch=batch_idx + 1,
                    total_batches=total_batches,
                )
            return batch_scored

    tasks = [
        _score_batch(batch, i)
        for i, batch in enumerate(batches)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for r in results:
        if isinstance(r, int):
            scored += r
        else:
            failed += 1

    logger.info(
        "scoring_complete",
        scored=scored,
        failed_batches=failed,
        total_batches=total_batches,
    )
    return scored


def _store_scores(
    conn: psycopg.Connection,
    company_id: str,
    form_c_text_id: str,
    scores: dict[str, Any],
) -> None:
    """Insert Claude text scores into the database."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO claude_text_scores (
                company_id, form_c_text_id,
                clarity, claims_plausibility, problem_specificity,
                differentiation_depth, founder_domain_signal,
                risk_honesty, business_model_clarity, text_quality_score,
                red_flags, reasoning, prompt_version, model_id
            ) VALUES (
                %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s
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
