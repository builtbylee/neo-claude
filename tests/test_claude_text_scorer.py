"""Tests for Claude text scorer prompt construction and response parsing."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from startuplens.scoring.claude_text_scorer import (
    MODEL_ID,
    PROMPT_VERSION,
    _SCORE_DIMENSIONS,
    build_scoring_prompt,
    score_text,
)


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


class TestBuildScoringPrompt:
    def test_returns_single_user_message(self):
        messages = build_scoring_prompt("Some offering text")
        assert len(messages) == 1
        assert messages[0]["role"] == "user"

    def test_includes_narrative_text(self):
        messages = build_scoring_prompt("AI-powered widget for SMBs")
        assert "AI-powered widget for SMBs" in messages[0]["content"]

    def test_includes_context(self):
        ctx = {"sector": "fintech", "funding_target": 100000}
        messages = build_scoring_prompt("Some text", context=ctx)
        assert "fintech" in messages[0]["content"]
        assert "100000" in messages[0]["content"]

    def test_truncates_long_text(self):
        long_text = "x" * 50_000
        messages = build_scoring_prompt(long_text)
        assert "[Text truncated for length]" in messages[0]["content"]

    def test_prompt_version_is_stable(self):
        assert len(PROMPT_VERSION) == 12
        assert PROMPT_VERSION.isalnum()


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def _mock_client(response_text: str) -> MagicMock:
    """Create a mock Anthropic client that returns the given text."""
    client = MagicMock()
    content_block = MagicMock()
    content_block.text = response_text
    response = MagicMock()
    response.content = [content_block]
    client.messages.create.return_value = response
    return client


class TestScoreText:
    def test_valid_response(self):
        resp = json.dumps({
            "clarity": 55,
            "claims_plausibility": 40,
            "problem_specificity": 60,
            "differentiation_depth": 45,
            "founder_domain_signal": 50,
            "risk_honesty": 35,
            "business_model_clarity": 65,
            "text_quality_score": 50,
            "red_flags": ["vague market claims"],
            "reasoning": "Decent but vague in places.",
        })
        client = _mock_client(resp)
        scores = score_text(client, "Some text")

        assert scores is not None
        assert scores["clarity"] == 55
        assert scores["text_quality_score"] == 50
        assert "vague market claims" in scores["red_flags"]

    def test_markdown_fenced_response(self):
        inner = json.dumps({
            "clarity": 50,
            "claims_plausibility": 50,
            "problem_specificity": 50,
            "differentiation_depth": 50,
            "founder_domain_signal": 50,
            "risk_honesty": 50,
            "business_model_clarity": 50,
            "text_quality_score": 50,
            "red_flags": [],
            "reasoning": "Average.",
        })
        resp = f"```json\n{inner}\n```"
        client = _mock_client(resp)
        scores = score_text(client, "Some text")
        assert scores is not None
        assert scores["clarity"] == 50

    def test_invalid_json(self):
        client = _mock_client("This is not JSON at all")
        scores = score_text(client, "Some text")
        assert scores is None

    def test_missing_dimension(self):
        resp = json.dumps({
            "clarity": 50,
            # Missing other dimensions
            "text_quality_score": 50,
            "red_flags": [],
            "reasoning": "Incomplete.",
        })
        client = _mock_client(resp)
        scores = score_text(client, "Some text")
        assert scores is None

    def test_out_of_range_score(self):
        resp = json.dumps({
            "clarity": 150,  # Out of range
            "claims_plausibility": 50,
            "problem_specificity": 50,
            "differentiation_depth": 50,
            "founder_domain_signal": 50,
            "risk_honesty": 50,
            "business_model_clarity": 50,
            "text_quality_score": 50,
            "red_flags": [],
            "reasoning": "Bad score.",
        })
        client = _mock_client(resp)
        scores = score_text(client, "Some text")
        assert scores is None

    def test_boundary_scores(self):
        resp = json.dumps({
            "clarity": 0,
            "claims_plausibility": 100,
            "problem_specificity": 0,
            "differentiation_depth": 100,
            "founder_domain_signal": 0,
            "risk_honesty": 100,
            "business_model_clarity": 0,
            "text_quality_score": 50,
            "red_flags": [],
            "reasoning": "Extreme.",
        })
        client = _mock_client(resp)
        scores = score_text(client, "Some text")
        assert scores is not None
        assert scores["clarity"] == 0
        assert scores["claims_plausibility"] == 100

    def test_all_dimensions_present(self):
        assert len(_SCORE_DIMENSIONS) == 8
        assert "text_quality_score" in _SCORE_DIMENSIONS
