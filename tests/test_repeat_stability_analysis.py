"""Tests for repeat stability analysis metrics (fallback-adjusted)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from run_repeat_stability_analysis import _compute_stability  # noqa: E402


def _decision(
    run_id: str,
    run_name: str,
    item_id: str,
    company: str,
    agent: str,
    rec: str,
    conviction: int = 50,
    is_fallback: bool = False,
    model_recommendation: str | None = None,
) -> dict:
    raw = {"is_fallback": is_fallback, "fallback_reason": ""}
    if model_recommendation is not None:
        raw["model_recommendation"] = model_recommendation
    return {
        "run_id": run_id,
        "run_name": run_name,
        "shadow_cycle_item_id": item_id,
        "company_name": company,
        "analyst_profile": agent,
        "recommendation_class": rec,
        "conviction": conviction,
        "raw_response": raw,
        "model_recommendation": model_recommendation,
    }


class TestComputeStability:
    def _ids(self):
        return ["r1", "r2", "r3"]

    def _names(self):
        return ["run1", "run2", "run3"]

    def test_perfect_stability_no_fallback(self) -> None:
        """All agents give same non-fallback rec => G1-G4 pass, G5/G6 may fail."""
        decisions = []
        for rid, rn in zip(self._ids(), self._names()):
            for iid in ["item-a", "item-b"]:
                for agent in ["conservative", "balanced", "aggressive"]:
                    decisions.append(
                        _decision(rid, rn, iid, f"Co-{iid}", agent, "watch", 50)
                    )

        result = _compute_stability(self._ids(), self._names(), decisions)
        assert result["fallbackRate"] == 0.0
        assert result["nonFallbackRate"] == 1.0
        assert result["nonFallbackMajorityAgreementRate"] == 1.0
        assert result["disagreementRateStddev"] == 0.0
        assert result["gates"]["G1_fallback_rate_le_25pct"]["pass"] is True
        assert result["gates"]["G2_nf_majority_agreement_ge_75pct"]["pass"] is True
        assert result["gates"]["G3_nf_agent_pairwise_ge_80pct"]["pass"] is True
        assert result["gates"]["G4_disagreement_stddev_le_010"]["pass"] is True

    def test_high_fallback_rate_fails_g1(self) -> None:
        """If >25% are fallback, G1 fails."""
        decisions = []
        for rid, rn in zip(self._ids(), self._names()):
            for agent in ["conservative", "balanced", "aggressive"]:
                # 2 of 3 agents are fallback => 66% fallback rate
                fb = agent != "aggressive"
                decisions.append(
                    _decision(rid, rn, "item-a", "A", agent, "watch", 50, is_fallback=fb)
                )

        result = _compute_stability(self._ids(), self._names(), decisions)
        assert result["fallbackRate"] > 0.25
        assert result["gates"]["G1_fallback_rate_le_25pct"]["pass"] is False

    def test_class_coverage_below_3_fails_g6(self) -> None:
        """Only 2 classes observed => G6 fails."""
        decisions = []
        for rid, rn in zip(self._ids(), self._names()):
            for agent in ["conservative", "balanced", "aggressive"]:
                decisions.append(
                    _decision(rid, rn, "item-a", "A", agent, "watch")
                )
                decisions.append(
                    _decision(rid, rn, "item-b", "B", agent, "pass")
                )

        result = _compute_stability(self._ids(), self._names(), decisions)
        assert result["classCoverageCount"] == 2
        assert result["gates"]["G6_class_coverage_ge_3"]["pass"] is False

    def test_three_classes_passes_g6(self) -> None:
        """3+ classes => G6 passes."""
        decisions = []
        recs = {"conservative": "pass", "balanced": "watch", "aggressive": "invest"}
        for rid, rn in zip(self._ids(), self._names()):
            for agent, rec in recs.items():
                decisions.append(
                    _decision(rid, rn, "item-a", "A", agent, rec)
                )

        result = _compute_stability(self._ids(), self._names(), decisions)
        assert result["classCoverageCount"] == 3
        assert result["gates"]["G6_class_coverage_ge_3"]["pass"] is True

    def test_non_fallback_pairwise_excludes_fallback(self) -> None:
        """Non-fallback pairwise only counts pairs where both are non-fallback."""
        decisions = []
        # r1: conservative=watch (non-fb), r2: conservative=invest (non-fb)
        decisions.append(_decision("r1", "run1", "item-a", "A", "conservative", "watch"))
        decisions.append(_decision("r2", "run2", "item-a", "A", "conservative", "invest"))
        # balanced: both fallback — excluded from non-fallback pairwise
        decisions.append(
            _decision("r1", "run1", "item-a", "A", "balanced", "watch", is_fallback=True)
        )
        decisions.append(
            _decision("r2", "run2", "item-a", "A", "balanced", "watch", is_fallback=True)
        )
        # aggressive: both non-fb, agree
        decisions.append(_decision("r1", "run1", "item-a", "A", "aggressive", "invest"))
        decisions.append(_decision("r2", "run2", "item-a", "A", "aggressive", "invest"))

        result = _compute_stability(["r1", "r2"], ["run1", "run2"], decisions)

        nf_pairwise = result["nonFallbackPerAgentPairwiseAgreement"]
        # conservative: 1 pair, disagree => 0.0
        assert nf_pairwise["conservative"]["run1 vs run2"] == 0.0
        # balanced: 0 non-fallback pairs => 0.0
        assert nf_pairwise["balanced"]["run1 vs run2"] == 0.0
        # aggressive: 1 pair, agree => 1.0
        assert nf_pairwise["aggressive"]["run1 vs run2"] == 1.0

    def test_model_alignment_coverage_zero(self) -> None:
        """Without evaluations, model alignment coverage is 0 and G5 fails."""
        decisions = []
        for rid, rn in zip(self._ids(), self._names()):
            for agent in ["conservative", "balanced", "aggressive"]:
                decisions.append(
                    _decision(rid, rn, "item-a", "A", agent, "watch")
                )

        result = _compute_stability(self._ids(), self._names(), decisions)
        assert result["modelAlignmentCoverage"] == 0.0
        assert result["gates"]["G5_model_alignment_coverage_ge_30pct"]["pass"] is False

    def test_model_alignment_coverage_non_zero(self) -> None:
        """Coverage is computed from model recommendation presence."""
        decisions = []
        for rid, rn in zip(self._ids(), self._names()):
            for agent in ["conservative", "balanced", "aggressive"]:
                decisions.append(
                    _decision(
                        rid,
                        rn,
                        "item-a",
                        "A",
                        agent,
                        "watch",
                        model_recommendation="watch",
                    )
                )
                decisions.append(_decision(rid, rn, "item-b", "B", agent, "watch"))

        result = _compute_stability(self._ids(), self._names(), decisions)
        assert result["modelAlignmentCoverage"] == 0.5
        assert result["gates"]["G5_model_alignment_coverage_ge_30pct"]["pass"] is True

    def test_per_agent_fallback_rate(self) -> None:
        decisions = []
        for rid, rn in zip(self._ids(), self._names()):
            # conservative: always fallback
            decisions.append(
                _decision(rid, rn, "item-a", "A", "conservative", "watch", is_fallback=True)
            )
            # balanced + aggressive: never fallback
            decisions.append(_decision(rid, rn, "item-a", "A", "balanced", "watch"))
            decisions.append(_decision(rid, rn, "item-a", "A", "aggressive", "invest"))

        result = _compute_stability(self._ids(), self._names(), decisions)
        assert result["perAgentFallbackRate"]["conservative"] == 1.0
        assert result["perAgentFallbackRate"]["balanced"] == 0.0
        assert result["perAgentFallbackRate"]["aggressive"] == 0.0

    def test_conviction_stdev_computed(self) -> None:
        decisions = [
            _decision("r1", "run1", "item-a", "A", "conservative", "watch", 40),
            _decision("r1", "run1", "item-b", "B", "conservative", "watch", 60),
            _decision("r2", "run2", "item-a", "A", "conservative", "watch", 50),
            _decision("r2", "run2", "item-b", "B", "conservative", "watch", 50),
            # Need balanced + aggressive
            _decision("r1", "run1", "item-a", "A", "balanced", "watch", 50),
            _decision("r1", "run1", "item-b", "B", "balanced", "watch", 50),
            _decision("r2", "run2", "item-a", "A", "balanced", "watch", 50),
            _decision("r2", "run2", "item-b", "B", "balanced", "watch", 50),
            _decision("r1", "run1", "item-a", "A", "aggressive", "watch", 50),
            _decision("r1", "run1", "item-b", "B", "aggressive", "watch", 50),
            _decision("r2", "run2", "item-a", "A", "aggressive", "watch", 50),
            _decision("r2", "run2", "item-b", "B", "aggressive", "watch", 50),
        ]
        result = _compute_stability(["r1", "r2"], ["run1", "run2"], decisions)
        drift = result["nonFallbackConvictionDriftByAgent"]["conservative"]
        assert drift["run1"]["mean"] == 50.0
        assert drift["run1"]["stdev"] == pytest.approx(14.14, abs=0.1)
        assert drift["run2"]["stdev"] == 0.0

    def test_overall_fails_if_any_gate_fails(self) -> None:
        """Even with good stability, if G5 fails (no model data), overall is FAIL."""
        decisions = []
        recs = {"conservative": "pass", "balanced": "watch", "aggressive": "invest"}
        for rid, rn in zip(self._ids(), self._names()):
            for agent, rec in recs.items():
                decisions.append(_decision(rid, rn, "item-a", "A", agent, rec))

        result = _compute_stability(self._ids(), self._names(), decisions)
        # G5 definitely fails (no model alignment data)
        assert result["gates"]["G5_model_alignment_coverage_ge_30pct"]["pass"] is False
        assert result["overallPass"] is False
