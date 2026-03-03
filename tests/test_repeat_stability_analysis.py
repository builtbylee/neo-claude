"""Tests for repeat stability analysis metrics."""

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
) -> dict:
    return {
        "run_id": run_id,
        "run_name": run_name,
        "shadow_cycle_item_id": item_id,
        "company_name": company,
        "analyst_profile": agent,
        "recommendation_class": rec,
        "conviction": conviction,
    }


class TestComputeStability:
    def _ids(self):
        return ["r1", "r2", "r3"]

    def _names(self):
        return ["run1", "run2", "run3"]

    def test_perfect_stability(self) -> None:
        """All agents give same rec in every run => all gates pass."""
        decisions = []
        for rid, rn in zip(self._ids(), self._names()):
            for iid in ["item-a", "item-b"]:
                for agent in ["conservative", "balanced", "aggressive"]:
                    decisions.append(
                        _decision(rid, rn, iid, f"Co-{iid}", agent, "watch", 50)
                    )

        result = _compute_stability(self._ids(), self._names(), decisions)
        assert result["majorityAgreementRate"] == 1.0
        assert result["recommendationDriftCount"] == 0
        assert result["disagreementRateStddev"] == 0.0
        assert result["overallPass"] is True
        assert result["gates"]["G1_majority_agreement_ge_80pct"]["pass"] is True
        assert result["gates"]["G2_per_agent_pairwise_ge_85pct"]["pass"] is True
        assert result["gates"]["G3_disagreement_stddev_le_010"]["pass"] is True

    def test_total_instability(self) -> None:
        """Every agent changes class in every run => gates fail."""
        recs_by_run = {"r1": "watch", "r2": "invest", "r3": "pass"}
        decisions = []
        for rid, rn in zip(self._ids(), self._names()):
            for agent in ["conservative", "balanced", "aggressive"]:
                decisions.append(
                    _decision(rid, rn, "item-a", "Co-A", agent, recs_by_run[rid])
                )

        result = _compute_stability(self._ids(), self._names(), decisions)
        # Majority is unanimous within each run, but differs across runs
        assert result["majorityAgreementRate"] == 0.0
        assert result["recommendationDriftCount"] == 1
        # Per-agent pairwise: all agents differ between each pair
        for agent, pairs in result["perAgentPairwiseAgreement"].items():
            for pair, val in pairs.items():
                assert val == 0.0

    def test_partial_drift(self) -> None:
        """2 items: one stable, one drifts => 50% majority agreement."""
        decisions = []
        for rid, rn in zip(self._ids(), self._names()):
            for agent in ["conservative", "balanced", "aggressive"]:
                # item-a: always watch
                decisions.append(
                    _decision(rid, rn, "item-a", "StableCo", agent, "watch")
                )
            # item-b: r1/r2=invest, r3=pass
            rec = "invest" if rid != "r3" else "pass"
            for agent in ["conservative", "balanced", "aggressive"]:
                decisions.append(
                    _decision(rid, rn, "item-b", "DriftCo", agent, rec)
                )

        result = _compute_stability(self._ids(), self._names(), decisions)
        assert result["majorityAgreementRate"] == 0.5
        assert result["recommendationDriftCount"] == 1

    def test_disagreement_stddev_varies(self) -> None:
        """Different within-run disagreement rates across runs."""
        decisions = []
        # r1: all agents agree on watch for both items => disagreement=0
        for agent in ["conservative", "balanced", "aggressive"]:
            decisions.append(_decision("r1", "run1", "item-a", "A", agent, "watch"))
            decisions.append(_decision("r1", "run1", "item-b", "B", agent, "watch"))
        # r2: agents disagree on item-a => disagreement=0.5
        decisions.append(_decision("r2", "run2", "item-a", "A", "conservative", "watch"))
        decisions.append(_decision("r2", "run2", "item-a", "A", "balanced", "invest"))
        decisions.append(_decision("r2", "run2", "item-a", "A", "aggressive", "invest"))
        for agent in ["conservative", "balanced", "aggressive"]:
            decisions.append(_decision("r2", "run2", "item-b", "B", agent, "watch"))
        # r3: agents disagree on both items => disagreement=1.0
        decisions.append(_decision("r3", "run3", "item-a", "A", "conservative", "pass"))
        decisions.append(_decision("r3", "run3", "item-a", "A", "balanced", "invest"))
        decisions.append(_decision("r3", "run3", "item-a", "A", "aggressive", "watch"))
        decisions.append(_decision("r3", "run3", "item-b", "B", "conservative", "pass"))
        decisions.append(_decision("r3", "run3", "item-b", "B", "balanced", "invest"))
        decisions.append(_decision("r3", "run3", "item-b", "B", "aggressive", "watch"))

        result = _compute_stability(["r1", "r2", "r3"], ["run1", "run2", "run3"], decisions)
        assert result["perRunDisagreementRate"]["run1"] == 0.0
        assert result["perRunDisagreementRate"]["run2"] == 0.5
        assert result["perRunDisagreementRate"]["run3"] == 1.0
        assert result["disagreementRateStddev"] == pytest.approx(0.5, abs=0.001)

    def test_conviction_drift_summary(self) -> None:
        """Check mean/median conviction computation."""
        decisions = [
            _decision("r1", "run1", "item-a", "A", "conservative", "watch", 40),
            _decision("r1", "run1", "item-b", "B", "conservative", "watch", 60),
            _decision("r2", "run2", "item-a", "A", "conservative", "watch", 50),
            _decision("r2", "run2", "item-b", "B", "conservative", "watch", 50),
            # Need balanced + aggressive to avoid KeyError
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
        cons = result["convictionDriftByAgent"]["conservative"]
        assert cons["run1"]["mean"] == 50.0
        assert cons["run1"]["median"] == 50.0
        assert cons["run2"]["mean"] == 50.0
