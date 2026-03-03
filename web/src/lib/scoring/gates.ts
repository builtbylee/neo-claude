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
  enforceReliabilityGates?: boolean;
  valuationConfidence?: "high" | "medium" | "low" | null;
  segmentEvidence?: {
    segmentKey: string;
    sampleSize: number;
    survivalAuc: number | null;
    calibrationEce: number | null;
    releaseGateOpen: boolean;
  } | null;
  quarterlyEvidence?: {
    releaseReadiness: boolean;
    reportQuarter: string | null;
    isFresh: boolean;
  } | null;
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
  const enforceReliabilityGates = input.enforceReliabilityGates ?? true;

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

  // Gate 3: Segment evidence quality (US/UK x stage)
  if (enforceReliabilityGates) {
    if (input.segmentEvidence) {
      const evidence = input.segmentEvidence;
      const evidencePassed =
        evidence.sampleSize >= 200
        && evidence.releaseGateOpen
        && evidence.survivalAuc !== null
        && evidence.survivalAuc >= 0.65
        && evidence.calibrationEce !== null
        && evidence.calibrationEce <= 0.10;
      results.push({
        name: "Segment Evidence",
        passed: evidencePassed,
        value: `${evidence.segmentKey} n=${evidence.sampleSize}`,
        threshold: "n>=200, AUC>=0.65, ECE<=0.10, release gate open",
        action: evidencePassed ? "pass" : "abstain",
        reason: evidencePassed
          ? "Sufficient out-of-sample evidence for this segment"
          : "Segment evidence is insufficient for reliable autonomous recommendation",
      });
    } else {
      results.push({
        name: "Segment Evidence",
        passed: false,
        value: null,
        threshold: "segment evidence required",
        action: "abstain",
        reason: "No segment-specific backtest evidence available",
      });
    }

    // Gate 4: Valuation confidence quality
    const valuationConfidence = input.valuationConfidence ?? "low";
    const valuationPassed = valuationConfidence !== "low";
    results.push({
      name: "Valuation Confidence",
      passed: valuationPassed,
      value: valuationConfidence,
      threshold: "medium or high",
      action: valuationPassed ? "pass" : "abstain",
      reason: valuationPassed
        ? "Valuation context has sufficient coverage"
        : "Valuation context is low-confidence; escalate to deep diligence",
    });

    // Gate 5: Quarterly evidence freshness and release readiness.
    if (input.quarterlyEvidence) {
      const evidenceReady = (
        input.quarterlyEvidence.releaseReadiness
        && input.quarterlyEvidence.isFresh
      );
      results.push({
        name: "Quarterly Evidence",
        passed: evidenceReady,
        value: input.quarterlyEvidence.reportQuarter,
        threshold: "release_ready && current quarter",
        action: evidenceReady ? "pass" : "abstain",
        reason: evidenceReady
          ? "Quarterly evidence report is current and release-ready"
          : "Quarterly evidence is stale or release gate is closed",
      });
    } else {
      results.push({
        name: "Quarterly Evidence",
        passed: false,
        value: null,
        threshold: "latest quarterly report required",
        action: "abstain",
        reason: "No quarterly evidence report available",
      });
    }
  }

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
