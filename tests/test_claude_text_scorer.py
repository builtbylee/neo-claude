"""Tests for Claude text scorer batch pipeline and response parsing."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from startuplens.scoring.claude_text_scorer import (
    _SCORE_DIMENSIONS,
    BATCH_SIZE,
    MODEL_ID,
    PROMPT_VERSION,
    score_batch,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_prompt_version_is_stable_hash(self):
        assert len(PROMPT_VERSION) == 12
        assert PROMPT_VERSION.isalnum()

    def test_model_id_is_set(self):
        assert MODEL_ID.startswith("claude-")

    def test_batch_size_positive(self):
        assert BATCH_SIZE > 0

    def test_all_dimensions_present(self):
        assert len(_SCORE_DIMENSIONS) == 8
        assert "text_quality_score" in _SCORE_DIMENSIONS
        assert "clarity" in _SCORE_DIMENSIONS


# ---------------------------------------------------------------------------
# score_batch
# ---------------------------------------------------------------------------


def _mock_settings(api_key: str = "test-key") -> MagicMock:
    settings = MagicMock()
    settings.anthropic_api_key = api_key
    return settings


class TestScoreBatch:
    def test_returns_zero_when_no_api_key(self):
        conn = MagicMock()
        settings = _mock_settings(api_key="")
        result = score_batch(conn, settings)
        assert result == 0

    def test_returns_zero_when_no_texts(self):
        conn = MagicMock()
        settings = _mock_settings()
        with patch(
            "startuplens.scoring.claude_text_scorer._get_texts_to_score",
            return_value=[],
        ):
            result = score_batch(conn, settings)
        assert result == 0

    def test_batches_texts_correctly(self):
        """Verify texts are grouped into batches of BATCH_SIZE."""
        conn = MagicMock()
        settings = _mock_settings()

        texts = [
            {
                "form_c_text_id": f"t{i}",
                "company_id": f"c{i}",
                "narrative_text": f"Company {i} offering",
                "company_name": f"Co {i}",
            }
            for i in range(BATCH_SIZE + 3)
        ]

        with (
            patch(
                "startuplens.scoring.claude_text_scorer._get_texts_to_score",
                return_value=texts,
            ),
            patch(
                "startuplens.scoring.claude_text_scorer._score_batches_async",
                new_callable=AsyncMock,
                return_value=len(texts),
            ) as mock_async,
        ):
            result = score_batch(conn, settings)

        # Should have been called with 2 batches
        call_args = mock_async.call_args
        batches = call_args[0][2]  # third positional arg
        assert len(batches) == 2
        assert len(batches[0]) == BATCH_SIZE
        assert len(batches[1]) == 3
        assert result == len(texts)

    def test_commits_after_scoring(self):
        conn = MagicMock()
        settings = _mock_settings()

        with (
            patch(
                "startuplens.scoring.claude_text_scorer._get_texts_to_score",
                return_value=[{
                    "form_c_text_id": "t1",
                    "company_id": "c1",
                    "narrative_text": "text",
                    "company_name": "Co",
                }],
            ),
            patch(
                "startuplens.scoring.claude_text_scorer._score_batches_async",
                new_callable=AsyncMock,
                return_value=1,
            ),
        ):
            score_batch(conn, settings)

        conn.commit.assert_called_once()
