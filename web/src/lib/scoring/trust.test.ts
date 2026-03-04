import assert from "node:assert/strict";
import test from "node:test";

import { auditTermSignals, computeAnalystReadiness } from "./trust";

test("auditTermSignals flags conflicting structured vs derived fields", () => {
  const audit = auditTermSignals(
    {
      valuation_cap: 8_000_000,
      discount_rate: 0.15,
      interest_rate: null,
      maturity_date: null,
      liquidation_preference_multiple: 1,
      liquidation_participation: "non_participating",
      pro_rata_rights: true,
    },
    {
      valuationCap: 10_000_000,
      discountRate: 0.2,
      interestRate: null,
      maturityDate: null,
      liquidationPreferenceMultiple: 1,
      liquidationParticipation: "non_participating",
      proRataRights: true,
      confidence: "high",
    },
  );

  assert.ok(audit.conflicts.includes("valuation_cap"));
  assert.ok(audit.conflicts.includes("discount_rate"));
  assert.ok(audit.confidencePenalty >= 2);
});

test("auditTermSignals penalizes low-confidence derived-only terms", () => {
  const audit = auditTermSignals(
    null,
    {
      valuationCap: 9_000_000,
      discountRate: null,
      interestRate: null,
      maturityDate: null,
      liquidationPreferenceMultiple: null,
      liquidationParticipation: null,
      proRataRights: null,
      confidence: "medium",
    },
  );

  assert.equal(audit.fieldSources.valuation_cap, "derived_text");
  assert.ok(audit.confidencePenalty >= 1);
});

test("computeAnalystReadiness returns ready for strong evidence", () => {
  const readiness = computeAnalystReadiness({
    dataCompleteness: 0.85,
    failedGateCount: 0,
    valuationConfidence: "high",
    segmentEvidenceOk: true,
    quarterlyEvidenceOk: true,
    sourceTier: "A",
    pricingSampleSize: 300,
    stageCountrySectorComps: 120,
    tierAShare: 0.7,
    coreTermCompleteness: 0.9,
    valuationCriticalConflictCount: 0,
    operationalWarningCount: 0,
  });

  assert.equal(readiness.status, "ready");
  assert.equal(readiness.reasons.length, 0);
});

test("computeAnalystReadiness returns blocked when critical checks fail", () => {
  const readiness = computeAnalystReadiness({
    dataCompleteness: 0.45,
    failedGateCount: 2,
    valuationConfidence: "low",
    segmentEvidenceOk: false,
    quarterlyEvidenceOk: false,
    sourceTier: "C",
    pricingSampleSize: 40,
    stageCountrySectorComps: 20,
    tierAShare: 0.0,
    coreTermCompleteness: 0.2,
    valuationCriticalConflictCount: 2,
    operationalWarningCount: 1,
  });

  assert.equal(readiness.status, "blocked");
  assert.ok(readiness.reasons.length >= 4);
});
