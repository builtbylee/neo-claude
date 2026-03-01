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
text_quality_score (0-100). Dimensions:
1. CLARITY: Business model focus (name, sector, financials)
2. CLAIMS_PLAUSIBILITY: Financial trajectory plausibility
3. PROBLEM_SPECIFICITY: Evidence of real market need
4. DIFFERENTIATION_DEPTH: Competitive moat signals
5. FOUNDER_DOMAIN_SIGNAL: Execution capability indicators
6. RISK_HONESTY: Financial risk severity (inverse: lower = more risky)
7. BUSINESS_MODEL_CLARITY: Revenue pattern coherence

--- COMPANY ---
{profile}

Return a JSON object:
{"clarity": N, "claims_plausibility": N, "problem_specificity": N, \
"differentiation_depth": N, "founder_domain_signal": N, "risk_honesty": N, \
"business_model_clarity": N, "text_quality_score": N}`;

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

/**
 * Score a company's pitch text using Claude Haiku.
 *
 * Returns dimension scores or null if the API call or parsing fails.
 */
export async function scoreText(
  apiKey: string,
  profile: string,
): Promise<TextScores | null> {
  const client = new Anthropic({ apiKey });

  const prompt = USER_TEMPLATE.replace("{profile}", profile);

  const response = await client.messages.create({
    model: "claude-haiku-4-5-20251001",
    max_tokens: 512,
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
    return validateScores(parsed);
  } catch {
    return null;
  }
}
