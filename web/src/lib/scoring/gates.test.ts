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

test("checkGates fails term consistency on multiple conflicts", () => {
  const gates = checkGates({
    dataCompleteness: 0.9,
    modelScore: 75,
    confidenceRange: 10,
    isQuickScore: true,
    enforceReliabilityGates: false,
    termConflictCount: 2,
  });
  const termGate = gates.find((g) => g.name === "Term Consistency");
  assert.ok(termGate);
  assert.equal(termGate?.passed, false);
  assert.equal(termGate?.action, "abstain");
});
