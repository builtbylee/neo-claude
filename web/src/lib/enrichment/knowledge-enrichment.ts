/**
 * Claude knowledge-based company enrichment.
 *
 * Single Claude Haiku call that generates a company profile from training
 * knowledge, scores on 7 dimensions, and extracts structured facts.
 * Returns an `unknown` flag when Claude doesn't recognise the company.
 */

import Anthropic from "@anthropic-ai/sdk";
import type { TextScores, ExtractedFacts, TextScoringResult } from "@/lib/claude/text-scorer";

export interface KnowledgeResult extends TextScoringResult {
  unknown: boolean;
  generatedProfile: string | null;
}

const SYSTEM_PROMPT = `You are evaluating the investment quality of companies based on your training knowledge.

IMPORTANT: You may not have information about this company. If you do not recognise the \
company or have no substantive knowledge about it, you MUST set "unknown" to true. \
Do NOT fabricate details about companies you don't know.

Calibration guidance:
- Most crowdfunding companies score 30-50 on each dimension.
- Scores above 70 indicate genuinely strong indicators you are confident about.
- Scores below 20 indicate serious concerns.
- ~85% of equity crowdfunding companies fail. Be calibrated accordingly.
- When working from general knowledge rather than a pitch document, apply wider \
uncertainty — bias scores toward the 30-50 range unless you have specific knowledge.

Return ONLY valid JSON with no additional text or markdown formatting.`;

function buildKnowledgePrompt(
  companyName: string,
  sector?: string | null,
  revenue?: number | null,
  fundingTarget?: number | null,
): string {
  const parts = [`Company: ${companyName}`];
  if (sector) parts.push(`Sector: ${sector}`);
  if (revenue !== null && revenue !== undefined) {
    parts.push(`Revenue (user-provided): $${revenue.toLocaleString()}`);
  }
  if (fundingTarget !== null && fundingTarget !== undefined) {
    parts.push(`Funding Target (user-provided): $${fundingTarget.toLocaleString()}`);
  }

  return `Based on your training knowledge, describe what you know about the company below, \
then score it on 7 dimensions (0-100 each) plus an aggregate text_quality_score.

If you have no knowledge of this company, set "unknown" to true and return baseline \
scores of 35 for all dimensions with null for all extracted fields.

${parts.join("\n")}

First, write a brief profile (2-4 sentences) of what you know about this company — \
its product, market position, revenue scale, funding history, and any notable facts.

Then score based on that profile.

Scoring dimensions:
1. CLARITY: Business model focus
2. CLAIMS_PLAUSIBILITY: Financial trajectory plausibility
3. PROBLEM_SPECIFICITY: Evidence of real market need
4. DIFFERENTIATION_DEPTH: Competitive moat signals
5. FOUNDER_DOMAIN_SIGNAL: Execution capability indicators
6. RISK_HONESTY: Financial risk severity (inverse: lower = more risky)
7. BUSINESS_MODEL_CLARITY: Revenue pattern coherence

Data extraction — provide these if known (use null if not known):
- revenue: Annual revenue in USD
- funding_target: Most recent raise amount in USD
- revenue_growth_yoy: Year-over-year revenue growth as a decimal (e.g. 1.5 = 150%)
- employee_count: Number of employees
- company_age_months: Company age in months

Return a JSON object:
{"unknown": false, "profile": "Brief company description...", \
"scores": {"clarity": N, "claims_plausibility": N, "problem_specificity": N, \
"differentiation_depth": N, "founder_domain_signal": N, "risk_honesty": N, \
"business_model_clarity": N, "text_quality_score": N}, \
"extracted": {"revenue": N_or_null, "funding_target": N_or_null, \
"revenue_growth_yoy": N_or_null, "employee_count": N_or_null, \
"company_age_months": N_or_null}}`;
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
    if (typeof val !== "number" || val < 0 || val > 100) return null;
    result[dim] = Math.round(val);
  }
  return result as unknown as TextScores;
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

export async function scoreFromKnowledge(
  apiKey: string,
  companyName: string,
  sector?: string | null,
  revenue?: number | null,
  fundingTarget?: number | null,
): Promise<KnowledgeResult | null> {
  const client = new Anthropic({ apiKey });

  const prompt = buildKnowledgePrompt(companyName, sector, revenue, fundingTarget);

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

  const match = rawText.match(/\{[\s\S]*\}/);
  if (!match) return null;

  try {
    const parsed = JSON.parse(match[0]);

    const unknown = parsed.unknown === true;
    const generatedProfile =
      typeof parsed.profile === "string" ? parsed.profile : null;

    if (!parsed.scores || typeof parsed.scores !== "object") return null;
    const scores = validateScores(parsed.scores);
    if (!scores) return null;

    const extractedFacts = parsed.extracted
      ? parseExtractedFacts(parsed.extracted)
      : { revenue: null, fundingTarget: null, revenueGrowthYoy: null, employeeCount: null, companyAgeMonths: null };

    return {
      unknown,
      generatedProfile,
      scores,
      extractedFacts,
    };
  } catch {
    return null;
  }
}
