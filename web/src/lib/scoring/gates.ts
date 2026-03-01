/**
 * Abstention gates and kill criteria from ARCHITECTURE.md Section 3d.
 *
 * A deal cannot receive a positive recommendation unless ALL gates pass.
 * Kill criteria override any score.
 */

export interface GateResult {
  name: string;
  passed: boolean;
  value: number | string | null;
  threshold: string;
  action: "pass" | "abstain" | "manual_review" | "reject" | "flag";
  reason: string;
}

export interface GateCheckInput {
  dataCompleteness: number;
  modelScore: number;
  confidenceRange: number;
  isQuickScore: boolean;
  // Kill criteria
  directorDisqualified?: boolean;
  underAdministration?: boolean;
  sanctionsMatch?: boolean;
  accountsOverdue12Mo?: boolean;
}

/**
 * Compute normalised entropy from a probability.
 * H = -p*log2(p) - (1-p)*log2(1-p), normalised to [0,1].
 */
function normalizedEntropy(p: number): number {
  if (p <= 0 || p >= 1) return 0;
  const h = -(p * Math.log2(p) + (1 - p) * Math.log2(1 - p));
  return h; // Already normalized for binary case (max = 1.0)
}

/** Run all abstention gates. */
export function checkGates(input: GateCheckInput): GateResult[] {
  const results: GateResult[] = [];
  const minCompleteness = input.isQuickScore ? 0.40 : 0.60;

  // Gate 1: Data completeness
  results.push({
    name: "Data Completeness",
    passed: input.dataCompleteness >= minCompleteness,
    value: Math.round(input.dataCompleteness * 100),
    threshold: `>= ${minCompleteness * 100}%`,
    action: input.dataCompleteness >= minCompleteness ? "pass" : "abstain",
    reason:
      input.dataCompleteness >= minCompleteness
        ? "Sufficient data available"
        : "Insufficient data to make a reliable assessment",
  });

  // Gate 2: Model confidence (entropy)
  const pFail = 1 - input.modelScore / 100;
  const entropy = normalizedEntropy(pFail);
  const entropyPassed = entropy < 0.70;
  results.push({
    name: "Model Confidence",
    passed: entropyPassed,
    value: Math.round(entropy * 100) / 100,
    threshold: "entropy < 0.70",
    action: entropyPassed ? "pass" : "abstain",
    reason: entropyPassed
      ? "Model can distinguish this deal from base rate"
      : "Model cannot reliably distinguish from base rate",
  });

  // Kill criteria
  if (input.directorDisqualified) {
    results.push({
      name: "Director Disqualification",
      passed: false,
      value: "disqualified",
      threshold: "not disqualified",
      action: "reject",
      reason: "Director on disqualification register — compliance fail",
    });
  }

  if (input.underAdministration) {
    results.push({
      name: "Company Status",
      passed: false,
      value: "administration/liquidation",
      threshold: "active",
      action: "reject",
      reason: "Company under administration or liquidation",
    });
  }

  if (input.sanctionsMatch) {
    results.push({
      name: "Sanctions Screening",
      passed: false,
      value: "match",
      threshold: "no match",
      action: "reject",
      reason: "Sanctions match on director or owner",
    });
  }

  if (input.accountsOverdue12Mo) {
    results.push({
      name: "Accounts Overdue",
      passed: false,
      value: "> 12 months overdue",
      threshold: "current",
      action: "flag",
      reason: "Accounts overdue > 12 months — severe distress signal",
    });
  }

  return results;
}

/** Check if any gate resulted in a hard rejection. */
export function hasKillCriteria(gates: GateResult[]): boolean {
  return gates.some((g) => g.action === "reject");
}

/** Check if any gate requires abstention. */
export function shouldAbstain(gates: GateResult[]): boolean {
  return gates.some((g) => g.action === "abstain");
}

/** Check if all gates passed. */
export function allGatesPassed(gates: GateResult[]): boolean {
  return gates.every((g) => g.passed);
}
