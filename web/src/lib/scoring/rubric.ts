/**
 * 7-category weighted rubric scoring from ARCHITECTURE.md.
 *
 * Each category scored 0-100, weighted to produce an aggregate score.
 * Sceptical baseline starts at 35 (not 50) reflecting ECF's 8.5x
 * higher failure rate vs matched non-ECF companies.
 */

export interface CategoryScores {
  textNarrative: number;
  tractionGrowth: number;
  dealTerms: number;
  team: number;
  financialHealth: number;
  investmentSignal: number;
  market: number;
}

export interface RubricResult {
  overallScore: number;
  categories: CategoryScores;
  confidenceRange: number;
  dataCompleteness: number;
}

const CATEGORY_WEIGHTS = {
  textNarrative: 0.20,
  tractionGrowth: 0.18,
  dealTerms: 0.15,
  team: 0.15,
  financialHealth: 0.12,
  investmentSignal: 0.10,
  market: 0.10,
} as const;

const SCEPTICAL_BASELINE = 35;

export interface RubricInput {
  /** ML model survival score (0-100). */
  mlScore: number;
  /** Claude text quality score (0-100), null if no pitch text. */
  textScore: number | null;
  /** Claude dimension scores. */
  textDimensions: {
    clarity: number;
    claims_plausibility: number;
    problem_specificity: number;
    differentiation_depth: number;
    founder_domain_signal: number;
    risk_honesty: number;
    business_model_clarity: number;
  } | null;
  /** Company features for category scoring. */
  revenue: number | null;
  preRevenue: boolean;
  totalAssets: number | null;
  totalDebt: number | null;
  cashPosition: number | null;
  fundingTarget: number | null;
  amountRaised: number | null;
  overfundingRatio: number | null;
  hasInstitutionalCoinvestor: boolean;
  sector: string | null;
  revenueGrowthYoy: number | null;
  preMoneyValuation: number | null;
  instrumentType: string | null;
  investorCount: number | null;
  fundingVelocityDays: number | null;
  valuationContext: {
    valuationPercentile: number;
    impliedRevenueMultiple: number;
    cohortMedianMultiple: number;
    signal: "attractive" | "fair" | "aggressive";
  } | null;
}

function clamp(val: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, val));
}

/** Score the Text & Narrative category (20%). */
function scoreTextNarrative(input: RubricInput): number {
  if (!input.textScore || !input.textDimensions) {
    // No pitch text — return baseline
    return SCEPTICAL_BASELINE;
  }
  return clamp(input.textScore, 0, 100);
}

/** Score the Traction & Growth category (18%). */
function scoreTractionGrowth(input: RubricInput): number {
  let score = SCEPTICAL_BASELINE;

  // Pre-revenue penalty: cap at 30
  if (input.preRevenue) {
    return Math.min(score, 30);
  }

  // Revenue existence is a positive signal
  if (input.revenue && input.revenue > 0) {
    score += 15;
  }

  // Revenue growth YoY
  if (input.revenueGrowthYoy !== null) {
    if (input.revenueGrowthYoy > 1.0) score += 20; // >100% growth
    else if (input.revenueGrowthYoy > 0.5) score += 15;
    else if (input.revenueGrowthYoy > 0.2) score += 10;
    else if (input.revenueGrowthYoy > 0) score += 5;
    else score -= 5; // Declining revenue
  }

  // Overfunding as traction signal
  if (input.overfundingRatio !== null && input.overfundingRatio > 1.0) {
    score += Math.min(15, (input.overfundingRatio - 1.0) * 10);
  }

  return clamp(score, 0, 100);
}

/** Score the Deal Terms & Valuation category (15%). */
function scoreDealTerms(input: RubricInput): number {
  let score = SCEPTICAL_BASELINE;

  // Funding target reasonableness
  if (input.fundingTarget && input.amountRaised) {
    const ratio = input.amountRaised / input.fundingTarget;
    if (ratio >= 1.0) score += 10; // Hit target
    if (ratio >= 1.5) score += 5; // Well overfunded
  }

  if (input.valuationContext) {
    if (input.valuationContext.signal === "attractive") score += 20;
    else if (input.valuationContext.signal === "fair") score += 8;
    else if (input.valuationContext.signal === "aggressive") score -= 20;
  }

  if (input.instrumentType) {
    const instrument = input.instrumentType.toLowerCase();
    if (instrument.includes("equity")) score += 4;
    if (instrument.includes("safe") || instrument.includes("convertible")) score -= 4;
    if (instrument.includes("debt")) score -= 8;
  }

  if (input.investorCount !== null) {
    if (input.investorCount > 300) score -= 8;
    else if (input.investorCount > 150) score -= 4;
  }

  if (input.fundingVelocityDays !== null) {
    if (input.fundingVelocityDays <= 14) score += 8;
    else if (input.fundingVelocityDays <= 30) score += 4;
    else if (input.fundingVelocityDays > 90) score -= 4;
  }

  return clamp(score, 0, 100);
}

/** Score the Team category (15%). */
function scoreTeam(input: RubricInput): number {
  let score = SCEPTICAL_BASELINE;

  // Founder domain signal from Claude text analysis
  if (input.textDimensions) {
    const founderSignal = input.textDimensions.founder_domain_signal;
    score = SCEPTICAL_BASELINE + ((founderSignal - 50) / 50) * 30;
  }

  return clamp(score, 0, 100);
}

/** Score the Financial Health category (12%). */
function scoreFinancialHealth(input: RubricInput): number {
  let score = SCEPTICAL_BASELINE;

  // Debt-to-asset ratio
  if (input.totalDebt !== null && input.totalAssets !== null && input.totalAssets > 0) {
    const dta = input.totalDebt / input.totalAssets;
    if (dta < 0.3) score += 15;
    else if (dta < 0.5) score += 10;
    else if (dta < 0.8) score += 5;
    else score -= 10; // High leverage
  }

  // Cash position
  if (input.cashPosition !== null && input.cashPosition > 0) {
    score += 5;
    if (input.cashPosition > 100_000) score += 5;
  }

  return clamp(score, 0, 100);
}

/** Score the Investment Signal category (10%). */
function scoreInvestmentSignal(input: RubricInput): number {
  let score = SCEPTICAL_BASELINE;

  // Institutional co-investor bonus
  if (input.hasInstitutionalCoinvestor) {
    score += 15;
  }

  // Overfunding ratio as crowd signal
  if (input.overfundingRatio !== null) {
    if (input.overfundingRatio > 2.0) score += 10;
    else if (input.overfundingRatio > 1.5) score += 5;
  }

  return clamp(score, 0, 100);
}

/** Score the Market category (10%). */
function scoreMarket(input: RubricInput): number {
  let score = SCEPTICAL_BASELINE;

  // Market signals from Claude text analysis
  if (input.textDimensions) {
    const problemSpec = input.textDimensions.problem_specificity;
    const diffDepth = input.textDimensions.differentiation_depth;
    score = SCEPTICAL_BASELINE + (((problemSpec + diffDepth) / 2 - 50) / 50) * 25;
  }

  return clamp(score, 0, 100);
}

/** Count how many input fields have real data. */
function computeDataCompleteness(input: RubricInput): number {
  const fields = [
    input.revenue,
    input.totalAssets,
    input.totalDebt,
    input.cashPosition,
    input.fundingTarget,
    input.amountRaised,
    input.overfundingRatio,
    input.preMoneyValuation,
    input.instrumentType,
    input.investorCount,
    input.fundingVelocityDays,
    input.valuationContext?.impliedRevenueMultiple ?? null,
    input.valuationContext?.cohortMedianMultiple ?? null,
    input.sector,
    input.revenueGrowthYoy,
    input.textScore,
  ];
  const filled = fields.filter((f) => f !== null && f !== undefined).length;
  return filled / fields.length;
}

/** Confidence range based on data completeness. */
function computeConfidenceRange(completeness: number, hasText: boolean): number {
  if (completeness > 0.8 && hasText) return 8;
  if (completeness > 0.5 && hasText) return 15;
  if (completeness > 0.5) return 20;
  return 25;
}

/**
 * Compute the full rubric score from all available inputs.
 *
 * Blends the ML model score with category-specific assessments.
 * The ML score anchors the overall score; category scores modulate it.
 */
export function computeRubric(input: RubricInput): RubricResult {
  const categories: CategoryScores = {
    textNarrative: scoreTextNarrative(input),
    tractionGrowth: scoreTractionGrowth(input),
    dealTerms: scoreDealTerms(input),
    team: scoreTeam(input),
    financialHealth: scoreFinancialHealth(input),
    investmentSignal: scoreInvestmentSignal(input),
    market: scoreMarket(input),
  };

  // Weighted sum of category scores
  const weightedSum =
    categories.textNarrative * CATEGORY_WEIGHTS.textNarrative +
    categories.tractionGrowth * CATEGORY_WEIGHTS.tractionGrowth +
    categories.dealTerms * CATEGORY_WEIGHTS.dealTerms +
    categories.team * CATEGORY_WEIGHTS.team +
    categories.financialHealth * CATEGORY_WEIGHTS.financialHealth +
    categories.investmentSignal * CATEGORY_WEIGHTS.investmentSignal +
    categories.market * CATEGORY_WEIGHTS.market;

  // Blend ML score (60%) with rubric (40%) for the overall score
  const mlWeight = 0.6;
  const rubricWeight = 0.4;
  const overallScore = Math.round(
    input.mlScore * mlWeight + weightedSum * rubricWeight,
  );

  const dataCompleteness = computeDataCompleteness(input);
  const confidenceRange = computeConfidenceRange(
    dataCompleteness,
    input.textScore !== null,
  );

  return {
    overallScore: clamp(overallScore, 0, 100),
    categories,
    confidenceRange,
    dataCompleteness,
  };
}
