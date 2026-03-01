/**
 * Recommendation classification from ARCHITECTURE.md.
 *
 * Five classes: Invest, Deep Diligence, Watch, Pass, Abstain.
 */

import { type GateResult, allGatesPassed, hasKillCriteria, shouldAbstain } from "./gates";

export type RecommendationClass =
  | "invest"
  | "deep_diligence"
  | "watch"
  | "pass"
  | "abstain";

export interface Recommendation {
  class: RecommendationClass;
  label: string;
  description: string;
  score: number;
  confidenceRange: number;
  gates: GateResult[];
}

const LABELS: Record<RecommendationClass, { label: string; description: string }> = {
  invest: {
    label: "Invest",
    description: "All gates pass, strong score. Proceed with investment.",
  },
  deep_diligence: {
    label: "Deep Diligence",
    description: "Promising but warrants deeper investigation before committing.",
  },
  watch: {
    label: "Watch",
    description: "Interesting but data gaps or marginal score. Monitor for developments.",
  },
  pass: {
    label: "Pass",
    description: "Below threshold or kill criteria triggered. Track in anti-portfolio.",
  },
  abstain: {
    label: "Abstain",
    description: "Insufficient data to make a reliable assessment.",
  },
};

/**
 * Classify a deal into a recommendation class.
 *
 * Logic from ARCHITECTURE.md:
 * - Invest: All gates pass, score >= 65, no kill criteria
 * - Deep Diligence: All gates pass, score 50-65 OR one gate marginal
 * - Watch: Score 40-50 OR interesting with data gaps
 * - Pass: Score < 40 OR kill criteria triggered
 * - Abstain: One+ gates failed (data/confidence insufficient)
 */
export function classify(
  score: number,
  confidenceRange: number,
  gates: GateResult[],
): Recommendation {
  let recClass: RecommendationClass;

  if (hasKillCriteria(gates)) {
    recClass = "pass";
  } else if (shouldAbstain(gates)) {
    recClass = "abstain";
  } else if (allGatesPassed(gates) && score >= 65) {
    recClass = "invest";
  } else if (allGatesPassed(gates) && score >= 50) {
    recClass = "deep_diligence";
  } else if (score >= 40) {
    recClass = "watch";
  } else {
    recClass = "pass";
  }

  const meta = LABELS[recClass];

  return {
    class: recClass,
    label: meta.label,
    description: meta.description,
    score,
    confidenceRange,
    gates,
  };
}
