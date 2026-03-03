"""Tests for the multi-agent synthetic analyst pilot."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from run_synthetic_analyst_pilot_agents import (  # noqa: E402
    AGENT_PROFILES,
    AgentDecision,
    AgentState,
    _aggregate,
    _ask_persona_async,
    _fan_out_agents,
    _load_cycle_items_by_ids,
    _parse_json_response,
    _upsert_decision,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_decision(
    item_id: str = "item-1",
    company: str = "TestCo",
    profile: str = "balanced",
    rec: str = "watch",
    conviction: int = 60,
    model_rec: str | None = None,
) -> AgentDecision:
    return AgentDecision(
        shadow_cycle_item_id=item_id,
        company_name=company,
        analyst_profile=profile,
        recommendation_class=rec,
        conviction=conviction,
        rationale="test rationale",
        key_risks=["risk_a"],
        data_gaps=["gap_a"],
        raw_response={"recommendation_class": rec},
        model_recommendation=model_rec,
    )


def _mock_conn() -> MagicMock:
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    cursor.description = None
    cursor.fetchall.return_value = []
    return conn


def _make_claude_response(payload: dict) -> SimpleNamespace:
    """Build a fake Anthropic message response."""
    block = SimpleNamespace(type="text", text=json.dumps(payload))
    return SimpleNamespace(content=[block])


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------


class TestParseJsonResponse:
    def test_direct_json(self) -> None:
        parsed = _parse_json_response('{"recommendation_class":"invest","conviction":80}')
        assert parsed["recommendation_class"] == "invest"

    def test_markdown_wrapped(self) -> None:
        text = '```json\n{"recommendation_class":"pass","conviction":30}\n```'
        parsed = _parse_json_response(text)
        assert parsed["recommendation_class"] == "pass"

    def test_prose_around_json(self) -> None:
        text = 'Here is my analysis:\n{"recommendation_class":"watch","conviction":55}\nEnd.'
        parsed = _parse_json_response(text)
        assert parsed["conviction"] == 55

    def test_no_json_raises(self) -> None:
        with pytest.raises(ValueError, match="did not contain a JSON object"):
            _parse_json_response("No JSON here at all.")


# ---------------------------------------------------------------------------
# Parallel fan-out
# ---------------------------------------------------------------------------


class TestParallelFanOut:
    def test_three_agents_run_in_parallel(self) -> None:
        """All 3 agents produce independent decisions for each item."""
        items = [
            {
                "shadow_cycle_item_id": "item-1",
                "company_name": "AlphaCo",
                "model_recommendation": "watch",
                "score": 55,
                "confidence_level": "medium",
                "sector": "fintech",
                "country": "GB",
                "source": "deal_alert",
                "source_ref": None,
                "category_scores": None,
                "risk_flags": None,
                "missing_data_fields": None,
                "valuation_analysis": None,
                "return_distribution": None,
                "cf_platform": None,
                "campaign_date": None,
                "funding_target": 500000,
                "amount_raised": 250000,
                "pre_money_valuation": 2000000,
                "equity_offered": None,
                "cf_investor_count": None,
                "had_revenue": True,
                "revenue_at_raise": None,
                "stage_bucket": "seed",
                "round_date": None,
                "round_type": None,
                "instrument_type": None,
                "round_amount_raised": None,
                "round_pre_money_valuation": None,
                "round_investor_count": None,
                "qualified_institutional": None,
                "eis_seis_eligible": None,
                "qsbs_eligible": None,
            },
        ]

        fake_response = _make_claude_response(
            {
                "recommendation_class": "deep_diligence",
                "conviction": 65,
                "rationale": "Interesting company.",
                "key_risks": ["execution"],
                "data_gaps": ["revenue_detail"],
            }
        )

        async def mock_create(**kwargs):
            return fake_response

        with patch("anthropic.AsyncAnthropic") as mock_client_cls:
            instance = AsyncMock()
            instance.messages.create = mock_create
            mock_client_cls.return_value = instance

            states = asyncio.run(_fan_out_agents("fake-key", items))

        assert len(states) == 3
        profiles = {s.profile["id"] for s in states}
        assert profiles == {"conservative", "balanced", "aggressive"}

        for state in states:
            assert len(state.decisions) == 1
            assert state.decisions[0].recommendation_class == "deep_diligence"
            assert state.errors == 0


# ---------------------------------------------------------------------------
# Idempotent upsert
# ---------------------------------------------------------------------------


class TestIdempotentUpsert:
    def test_upsert_executes_on_conflict_query(self) -> None:
        conn = _mock_conn()
        decision = _make_decision()
        _upsert_decision(conn, "run-123", decision)

        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.execute.assert_called_once()
        query = cursor.execute.call_args[0][0]
        assert "ON CONFLICT" in query
        assert "DO UPDATE SET" in query

    def test_upsert_twice_same_args(self) -> None:
        """Calling upsert twice with identical args should not raise."""
        conn = _mock_conn()
        decision = _make_decision()
        _upsert_decision(conn, "run-123", decision)
        _upsert_decision(conn, "run-123", decision)

        cursor = conn.cursor.return_value.__enter__.return_value
        assert cursor.execute.call_count == 2


# ---------------------------------------------------------------------------
# Aggregation math
# ---------------------------------------------------------------------------


class TestAggregation:
    def _cycle(self) -> dict:
        return {"id": "cycle-1", "cycle_name": "test-cycle"}

    def test_unanimous_agreement(self) -> None:
        states = []
        for pid in ("conservative", "balanced", "aggressive"):
            profile = next(p for p in AGENT_PROFILES if p["id"] == pid)
            s = AgentState(profile=profile)
            s.decisions.append(_make_decision(profile=pid, rec="watch"))
            states.append(s)

        summary = _aggregate("test-run", self._cycle(), states)
        assert summary["disagreementRate"] == 0.0
        assert summary["majorityRecommendationDistribution"] == {"watch": 1}
        assert summary["totalItems"] == 1
        assert summary["totalDecisions"] == 3

    def test_full_disagreement(self) -> None:
        recs = ["invest", "watch", "pass"]
        states = []
        for pid, rec in zip(("conservative", "balanced", "aggressive"), recs):
            profile = next(p for p in AGENT_PROFILES if p["id"] == pid)
            s = AgentState(profile=profile)
            s.decisions.append(_make_decision(profile=pid, rec=rec))
            states.append(s)

        summary = _aggregate("test-run", self._cycle(), states)
        assert summary["disagreementRate"] == 1.0
        assert summary["totalDecisions"] == 3

    def test_model_alignment_when_match(self) -> None:
        states = []
        for pid in ("conservative", "balanced", "aggressive"):
            profile = next(p for p in AGENT_PROFILES if p["id"] == pid)
            s = AgentState(profile=profile)
            s.decisions.append(
                _make_decision(profile=pid, rec="invest", model_rec="invest")
            )
            states.append(s)

        summary = _aggregate("test-run", self._cycle(), states)
        assert summary["modelMajorityAlignmentRate"] == 1.0

    def test_model_alignment_no_model_rec(self) -> None:
        states = []
        for pid in ("conservative", "balanced", "aggressive"):
            profile = next(p for p in AGENT_PROFILES if p["id"] == pid)
            s = AgentState(profile=profile)
            s.decisions.append(_make_decision(profile=pid, rec="watch", model_rec=None))
            states.append(s)

        summary = _aggregate("test-run", self._cycle(), states)
        assert summary["modelMajorityAlignmentRate"] is None

    def test_per_agent_distribution(self) -> None:
        states = []
        for pid, rec in zip(
            ("conservative", "balanced", "aggressive"),
            ("pass", "watch", "invest"),
        ):
            profile = next(p for p in AGENT_PROFILES if p["id"] == pid)
            s = AgentState(profile=profile)
            s.decisions.append(_make_decision(profile=pid, rec=rec))
            states.append(s)

        summary = _aggregate("test-run", self._cycle(), states)
        assert summary["perAgentDistribution"]["conservative"] == {"pass": 1}
        assert summary["perAgentDistribution"]["balanced"] == {"watch": 1}
        assert summary["perAgentDistribution"]["aggressive"] == {"invest": 1}

    def test_error_count_aggregated(self) -> None:
        states = []
        for pid in ("conservative", "balanced", "aggressive"):
            profile = next(p for p in AGENT_PROFILES if p["id"] == pid)
            s = AgentState(profile=profile)
            s.decisions.append(_make_decision(profile=pid, rec="watch"))
            if pid == "aggressive":
                s.errors = 2
            states.append(s)

        summary = _aggregate("test-run", self._cycle(), states)
        assert summary["totalErrors"] == 2

    def test_multiple_items_disagreement(self) -> None:
        """2 items: one unanimous, one split => 50% disagreement."""
        states = []
        for pid, recs in [
            ("conservative", ["watch", "pass"]),
            ("balanced", ["watch", "watch"]),
            ("aggressive", ["watch", "invest"]),
        ]:
            profile = next(p for p in AGENT_PROFILES if p["id"] == pid)
            s = AgentState(profile=profile)
            for i, rec in enumerate(recs):
                s.decisions.append(
                    _make_decision(item_id=f"item-{i}", profile=pid, rec=rec)
                )
            states.append(s)

        summary = _aggregate("test-run", self._cycle(), states)
        assert summary["totalItems"] == 2
        assert summary["disagreementRate"] == 0.5


# ---------------------------------------------------------------------------
# Malformed JSON fallback
# ---------------------------------------------------------------------------


class TestMalformedJsonFallback:
    def test_retry_on_malformed_first_attempt(self) -> None:
        """First call returns garbage, second returns valid JSON."""
        call_count = 0

        async def mock_create(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                block = SimpleNamespace(type="text", text="Sorry, here is my analysis")
                return SimpleNamespace(content=[block])
            return _make_claude_response(
                {
                    "recommendation_class": "watch",
                    "conviction": 50,
                    "rationale": "retry worked",
                    "key_risks": [],
                    "data_gaps": [],
                }
            )

        client = AsyncMock()
        client.messages.create = mock_create
        persona = AGENT_PROFILES[1]  # balanced
        context = {
            "company": {"name": "Test"},
            "model_output": {"score": None},
            "deal_snapshot": {"latest_round": {}},
        }

        result = asyncio.run(_ask_persona_async(client, persona, context))
        assert result["recommendation_class"] == "watch"
        assert call_count == 2


# ---------------------------------------------------------------------------
# Partial agent failure
# ---------------------------------------------------------------------------


class TestPartialAgentFailure:
    def test_one_agent_fails_others_continue(self) -> None:
        """If one agent's API call fails for a deal, it records abstain; others succeed."""
        call_count = 0

        async def mock_create(**kwargs):
            nonlocal call_count
            call_count += 1
            # Fail for conservative agent (first 2 retries)
            system_text = kwargs.get("system", "")
            if "conservative" in system_text:
                raise RuntimeError("Simulated API failure")
            return _make_claude_response(
                {
                    "recommendation_class": "invest",
                    "conviction": 75,
                    "rationale": "looks good",
                    "key_risks": [],
                    "data_gaps": [],
                }
            )

        items = [
            {
                "shadow_cycle_item_id": "item-1",
                "company_name": "FailTestCo",
                "model_recommendation": None,
                "score": None,
                "confidence_level": None,
                "sector": None,
                "country": None,
                "source": "deal_alert",
                "source_ref": None,
                "category_scores": None,
                "risk_flags": None,
                "missing_data_fields": None,
                "valuation_analysis": None,
                "return_distribution": None,
                "cf_platform": None,
                "campaign_date": None,
                "funding_target": None,
                "amount_raised": None,
                "pre_money_valuation": None,
                "equity_offered": None,
                "cf_investor_count": None,
                "had_revenue": None,
                "revenue_at_raise": None,
                "stage_bucket": None,
                "round_date": None,
                "round_type": None,
                "instrument_type": None,
                "round_amount_raised": None,
                "round_pre_money_valuation": None,
                "round_investor_count": None,
                "qualified_institutional": None,
                "eis_seis_eligible": None,
                "qsbs_eligible": None,
            },
        ]

        with patch("anthropic.AsyncAnthropic") as mock_client_cls:
            instance = AsyncMock()
            instance.messages.create = mock_create
            mock_client_cls.return_value = instance

            states = asyncio.run(_fan_out_agents("fake-key", items))

        cons = next(s for s in states if s.profile["id"] == "conservative")
        bal = next(s for s in states if s.profile["id"] == "balanced")
        aggr = next(s for s in states if s.profile["id"] == "aggressive")

        # Conservative failed -> abstain with error marker
        assert cons.decisions[0].recommendation_class == "abstain"
        assert "persona_scoring_error" in cons.decisions[0].key_risks
        assert cons.errors == 1

        # Other agents succeeded
        assert bal.decisions[0].recommendation_class == "invest"
        assert bal.errors == 0
        assert aggr.decisions[0].recommendation_class == "invest"
        assert aggr.errors == 0


# ---------------------------------------------------------------------------
# Locked item set loading
# ---------------------------------------------------------------------------


class TestLoadCycleItemsByIds:
    def test_returns_items_in_input_order(self) -> None:
        """Items should be returned in the same order as the input IDs."""
        conn = _mock_conn()
        cursor = conn.cursor.return_value.__enter__.return_value
        # DB returns rows in a different order than requested
        cursor.description = True
        cursor.fetchall.return_value = [
            {"shadow_cycle_item_id": "id-b", "company_name": "B"},
            {"shadow_cycle_item_id": "id-a", "company_name": "A"},
        ]

        result = _load_cycle_items_by_ids(conn, "cycle-1", ["id-a", "id-b"])
        assert result[0]["shadow_cycle_item_id"] == "id-a"
        assert result[1]["shadow_cycle_item_id"] == "id-b"

    def test_filters_missing_ids(self) -> None:
        """If DB returns fewer rows than requested, missing IDs are dropped."""
        conn = _mock_conn()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.description = True
        cursor.fetchall.return_value = [
            {"shadow_cycle_item_id": "id-a", "company_name": "A"},
        ]

        result = _load_cycle_items_by_ids(conn, "cycle-1", ["id-a", "id-missing"])
        assert len(result) == 1
        assert result[0]["shadow_cycle_item_id"] == "id-a"

    def test_empty_ids_returns_empty(self) -> None:
        conn = _mock_conn()
        result = _load_cycle_items_by_ids(conn, "cycle-1", [])
        assert result == []

    def test_query_includes_in_clause(self) -> None:
        conn = _mock_conn()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.description = True
        cursor.fetchall.return_value = []

        _load_cycle_items_by_ids(conn, "cycle-1", ["id-x"])
        query = cursor.execute.call_args[0][0]
        assert "IN (" in query
