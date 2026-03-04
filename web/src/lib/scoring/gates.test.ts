import assert from "node:assert/strict";
import test from "node:test";

import { checkGates } from "./gates";

test("checkGates uses modelPFail when provided", () => {
  const gates = checkGates({
    dataCompleteness: 0.9,
    modelScore: 80,
    modelPFail: 0.5,
    confidenceRange: 8,
    isQuickScore: true,
    enforceReliabilityGates: false,
  });
  const modelGate = gates.find((g) => g.name === "Model Confidence");
  assert.ok(modelGate);
  assert.equal(modelGate?.passed, false);
});

test("checkGates fails term consistency on any conflicts", () => {
  const gates = checkGates({
    dataCompleteness: 0.9,
    modelScore: 75,
    confidenceRange: 10,
    isQuickScore: true,
    enforceReliabilityGates: false,
    termConflictCount: 1,
  });
  const termGate = gates.find((g) => g.name === "Term Consistency");
  assert.ok(termGate);
  assert.equal(termGate?.passed, false);
  assert.equal(termGate?.action, "abstain");
});

test("checkGates applies analyst valuation gate thresholds", () => {
  const gates = checkGates({
    dataCompleteness: 0.9,
    modelScore: 75,
    confidenceRange: 10,
    isQuickScore: true,
    valuationConfidence: "high",
    segmentEvidence: {
      segmentKey: "US_Seed",
      sampleSize: 350,
      survivalAuc: 0.7,
      calibrationEce: 0.05,
      releaseGateOpen: true,
    },
    quarterlyEvidence: {
      releaseReadiness: true,
      reportQuarter: "2026-01-01",
      isFresh: true,
    },
    valuationGateMetrics: {
      stageCountrySectorComps: 70,
      tierAShare: 0.31,
      coreTermCompleteness: 0.9,
      valuationCriticalConflicts: 0,
    },
    termConflictCount: 0,
  });

  const analystGate = gates.find((g) => g.name === "Analyst Valuation Gate");
  assert.ok(analystGate);
  assert.equal(analystGate?.passed, false);
  assert.equal(analystGate?.action, "abstain");
});
