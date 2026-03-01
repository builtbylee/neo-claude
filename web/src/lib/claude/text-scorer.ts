/**
 * Claude Stage 1 text scoring — port of claude_text_scorer.py.
 *
 * Scores pitch text on 7 dimensions (0-100 each) plus an aggregate
 * text_quality_score. Single-company mode for interactive use.
 */

import Anthropic from "@anthropic-ai/sdk";

const SYSTEM_PROMPT = `You are evaluating the investment quality of startups based on structured \
financial profiles from SEC Reg CF filings. Score each dimension 0-100.

Calibration guidance:
- Most crowdfunding companies score 30-50 on each dimension.
- Scores above 70 indicate genuinely strong indicators.
- Scores below 20 indicate serious concerns or missing data.
- ~85% of equity crowdfunding companies fail. Be calibrated accordingly.

Return ONLY valid JSON with no additional text or markdown formatting.`;

const USER_TEMPLATE = `Score the company below on 7 dimensions (0-100 each) plus an aggregate \
text_quality_score (0-100). Also extract any factual data mentioned in the text.

Scoring dimensions:
1. CLARITY: Business model focus (name, sector, financials)
2. CLAIMS_PLAUSIBILITY: Financial trajectory plausibility
3. PROBLEM_SPECIFICITY: Evidence of real market need
4. DIFFERENTIATION_DEPTH: Competitive moat signals
5. FOUNDER_DOMAIN_SIGNAL: Execution capability indicators
6. RISK_HONESTY: Financial risk severity (inverse: lower = more risky)
7. BUSINESS_MODEL_CLARITY: Revenue pattern coherence

Data extraction — extract these if mentioned (use null if not stated):
- revenue: Annual revenue in USD (convert from other currencies if needed, e.g. £ or €)
- funding_target: Amount the company is trying to raise in USD
- revenue_growth_yoy: Year-over-year revenue growth as a decimal (e.g. 1.5 = 150% growth)
- employee_count: Number of employees
- company_age_months: Approximate company age in months (estimate from founding date if given)

--- COMPANY ---
{profile}

Return a JSON object with two top-level keys:
{"scores": {"clarity": N, "claims_plausibility": N, "problem_specificity": N, \
"differentiation_depth": N, "founder_domain_signal": N, "risk_honesty": N, \
"business_model_clarity": N, "text_quality_score": N}, \
"extracted": {"revenue": N_or_null, "funding_target": N_or_null, \
"revenue_growth_yoy": N_or_null, "employee_count": N_or_null, \
"company_age_months": N_or_null}}`;

export interface ExtractedFacts {
  revenue: number | null;
  fundingTarget: number | null;
  revenueGrowthYoy: number | null;
  employeeCount: number | null;
  companyAgeMonths: number | null;
}

export interface TextScores {
  clarity: number;
  claims_plausibility: number;
  problem_specificity: number;
  differentiation_depth: number;
  founder_domain_signal: number;
  risk_honesty: number;
  business_model_clarity: number;
  text_quality_score: number;
}

export interface TextScoringResult {
  scores: TextScores;
  extractedFacts: ExtractedFacts;
}

const SCORE_DIMENSIONS: (keyof TextScores)[] = [
  "clarity",
  "claims_plausibility",
  "problem_specificity",
  "differentiation_depth",
  "founder_domain_signal",
  "risk_honesty",
  "business_model_clarity",
  "text_quality_score",
];

function validateScores(obj: Record<string, unknown>): TextScores | null {
  const result: Record<string, number> = {};
  for (const dim of SCORE_DIMENSIONS) {
    const val = obj[dim];
    if (typeof val !== "number" || val < 0 || val > 100) {
      return null;
    }
    result[dim] = Math.round(val);
  }
  return result as unknown as TextScores;
}

/**
 * Build a profile string from company data for Claude to evaluate.
 */
export function buildProfile(data: {
  name: string;
  sector?: string | null;
  revenue?: number | null;
  fundingTarget?: number | null;
  pitchText?: string | null;
}): string {
  const parts = [`Company: ${data.name}`];
  if (data.sector) parts.push(`Sector: ${data.sector}`);
  if (data.revenue !== null && data.revenue !== undefined) {
    parts.push(`Revenue: $${data.revenue.toLocaleString()}`);
  }
  if (data.fundingTarget !== null && data.fundingTarget !== undefined) {
    parts.push(`Funding Target: $${data.fundingTarget.toLocaleString()}`);
  }
  if (data.pitchText) {
    parts.push(`\nPitch:\n${data.pitchText}`);
  }
  return parts.join("\n");
}

function parseExtractedFacts(obj: Record<string, unknown>): ExtractedFacts {
  const numOrNull = (v: unknown): number | null =>
    typeof v === "number" && isFinite(v) ? v : null;
  return {
    revenue: numOrNull(obj.revenue),
    fundingTarget: numOrNull(obj.funding_target),
    revenueGrowthYoy: numOrNull(obj.revenue_growth_yoy),
    employeeCount: numOrNull(obj.employee_count),
    companyAgeMonths: numOrNull(obj.company_age_months),
  };
}

/**
 * Score a company's pitch text using Claude Haiku.
 *
 * Returns dimension scores + extracted facts, or null if the API call or parsing fails.
 */
export async function scoreText(
  apiKey: string,
  profile: string,
): Promise<TextScoringResult | null> {
  const client = new Anthropic({ apiKey });

  const prompt = USER_TEMPLATE.replace("{profile}", profile);

  const response = await client.messages.create({
    model: "claude-haiku-4-5-20251001",
    max_tokens: 768,
    system: SYSTEM_PROMPT,
    messages: [{ role: "user", content: prompt }],
  });

  let rawText =
    response.content[0].type === "text" ? response.content[0].text.trim() : "";

  // Strip markdown fences
  if (rawText.startsWith("```")) {
    rawText = rawText.split("\n").slice(1).join("\n");
    if (rawText.endsWith("```")) {
      rawText = rawText.slice(0, -3).trim();
    }
  }

  // Find JSON object in response
  const match = rawText.match(/\{[\s\S]*\}/);
  if (!match) return null;

  try {
    const parsed = JSON.parse(match[0]);

    // Handle new format: { scores: {...}, extracted: {...} }
    if (parsed.scores && typeof parsed.scores === "object") {
      const scores = validateScores(parsed.scores);
      if (!scores) return null;
      const extractedFacts = parsed.extracted
        ? parseExtractedFacts(parsed.extracted)
        : { revenue: null, fundingTarget: null, revenueGrowthYoy: null, employeeCount: null, companyAgeMonths: null };
      return { scores, extractedFacts };
    }

    // Fallback: old flat format (just scores, no extraction)
    const scores = validateScores(parsed);
    if (!scores) return null;
    return {
      scores,
      extractedFacts: { revenue: null, fundingTarget: null, revenueGrowthYoy: null, employeeCount: null, companyAgeMonths: null },
    };
  } catch {
    return null;
  }
}
