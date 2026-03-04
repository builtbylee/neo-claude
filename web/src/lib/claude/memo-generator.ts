/**
 * IC Memo generator — produces analyst-style investment memos via Claude.
 *
 * Takes all scoring outputs and synthesizes a structured memo with
 * thesis, evidence, risks, and missing-data analysis.
 */

import Anthropic from "@anthropic-ai/sdk";

export interface ICMemo {
  thesis: string;
  evidence: string[];
  risks: string[];
  missingData: MissingField[];
  diligenceChecklist: string[];
  verdict: string;
}

export interface MissingField {
  field: string;
  label: string;
  impact: string;
}

const SYSTEM_PROMPT = `You are a senior investment analyst writing a concise IC (Investment Committee) memo \
for an equity crowdfunding deal. Write in a direct, professional tone. \
Be calibrated: ~85% of equity crowdfunding companies fail within 5 years. \
Do not use marketing language. State facts and assessments plainly.

Return ONLY valid JSON with no additional text or markdown formatting.`;

function buildMemoPrompt(input: MemoInput): string {
  const parts: string[] = [];
  parts.push(`Company: ${input.companyName}`);
  if (input.sector) parts.push(`Sector: ${input.sector}`);
  if (input.matchedCompany) parts.push(`Database match: ${input.matchedCompany}`);

  parts.push(`\nOverall Score: ${input.score}/100 (confidence +/- ${input.confidenceRange})`);
  parts.push(`Recommendation: ${input.recommendationLabel} — ${input.recommendationDescription}`);

  parts.push("\nCategory Scores:");
  for (const [name, score] of Object.entries(input.categories)) {
    parts.push(`  ${name}: ${score}/100`);
  }

  if (input.textScores) {
    parts.push("\nAI Text Analysis Scores:");
    for (const [dim, score] of Object.entries(input.textScores)) {
      parts.push(`  ${dim.replace(/_/g, " ")}: ${score}/100`);
    }
  }

  if (input.extractedFacts) {
    parts.push("\nExtracted Data:");
    const ef = input.extractedFacts;
    if (ef.revenue !== null) parts.push(`  Revenue: $${ef.revenue.toLocaleString()}`);
    if (ef.fundingTarget !== null) parts.push(`  Funding Target: $${ef.fundingTarget.toLocaleString()}`);
    if (ef.revenueGrowthYoy !== null) parts.push(`  Revenue Growth YoY: ${Math.round(ef.revenueGrowthYoy * 100)}%`);
    if (ef.employeeCount !== null) parts.push(`  Employees: ${ef.employeeCount}`);
    if (ef.companyAgeMonths !== null) parts.push(`  Company Age: ${ef.companyAgeMonths} months`);
  }

  if (input.features) {
    parts.push("\nFinancial Data:");
    const f = input.features;
    if (f.revenue_at_raise) parts.push(`  Revenue at raise: $${Number(f.revenue_at_raise).toLocaleString()}`);
    if (f.total_assets) parts.push(`  Total assets: $${Number(f.total_assets).toLocaleString()}`);
    if (f.total_debt) parts.push(`  Total debt: $${Number(f.total_debt).toLocaleString()}`);
    if (f.cash_position) parts.push(`  Cash position: $${Number(f.cash_position).toLocaleString()}`);
    if (f.overfunding_ratio) parts.push(`  Overfunding ratio: ${Number(f.overfunding_ratio).toFixed(2)}x`);
  }

  if (input.failedGates.length > 0) {
    parts.push("\nFailed Gates:");
    for (const gate of input.failedGates) {
      parts.push(`  ${gate.name}: ${gate.reason}`);
    }
  }

  if (input.generatedProfile) {
    parts.push(`\nAI-Generated Profile: ${input.generatedProfile}`);
  }

  parts.push(`\nData Source: ${input.dataSource}`);
  parts.push(`Data Completeness: ${input.dataCompleteness}%`);
  if (input.valuationConfidence) {
    parts.push(`Valuation Confidence: ${input.valuationConfidence}`);
  }
  if (input.valuationConfidenceReason) {
    parts.push(`Valuation Confidence Reason: ${input.valuationConfidenceReason}`);
  }
  if (input.segmentEvidence) {
    parts.push(
      `Segment Evidence: ${input.segmentEvidence.segmentKey}, n=${input.segmentEvidence.sampleSize}, AUC=${input.segmentEvidence.survivalAuc ?? "n/a"}, ECE=${input.segmentEvidence.calibrationEce ?? "n/a"}, release_gate=${input.segmentEvidence.releaseGateOpen}, evidence_ok=${input.segmentEvidence.evidenceOk}`,
    );
  }
  if (input.provenanceSummary) {
    parts.push(
      `Data Provenance: newest_as_of=${input.provenanceSummary.newestAsOfDate ?? "unknown"}, stale=${input.provenanceSummary.stale}, tracked_fields=${input.provenanceSummary.fieldCount}`,
    );
  }

  const missingFieldsList = identifyMissingFields(input);
  if (missingFieldsList.length > 0) {
    parts.push("\nMissing Data Fields:");
    for (const mf of missingFieldsList) {
      parts.push(`  - ${mf.label}: ${mf.impact}`);
    }
  }

  return `Write an IC memo for this deal. Return a JSON object with these keys:
- "thesis": 1-2 sentence investment thesis (what makes this deal interesting or concerning)
- "evidence": array of 3-5 bullet points citing specific data from the profile
- "risks": array of 2-4 key risks, with the most critical first
- "verdict": 2-3 sentence final assessment with specific recommendation

Be specific — reference actual numbers and scores, not generalities.

--- DEAL DATA ---
${parts.join("\n")}`;
}

export interface MemoInput {
  companyName: string;
  sector: string | null;
  matchedCompany: string | null;
  score: number;
  confidenceRange: number;
  recommendationLabel: string;
  recommendationDescription: string;
  recommendationClass: string;
  categories: Record<string, number>;
  textScores: Record<string, number> | null;
  extractedFacts: {
    revenue: number | null;
    fundingTarget: number | null;
    revenueGrowthYoy: number | null;
    employeeCount: number | null;
    companyAgeMonths: number | null;
  } | null;
  features: Record<string, number | string | null> | null;
  failedGates: Array<{ name: string; reason: string }>;
  dataSource: string;
  dataCompleteness: number;
  generatedProfile: string | null;
  valuationConfidence?: "low" | "medium" | "high";
  valuationConfidenceReason?: string;
  segmentEvidence?: {
    segmentKey: string;
    sampleSize: number;
    survivalAuc: number | null;
    calibrationEce: number | null;
    releaseGateOpen: boolean;
    evidenceOk: boolean;
  } | null;
  provenanceSummary?: {
    newestAsOfDate: string | null;
    stale: boolean;
    fieldCount: number;
  } | null;
}

/**
 * Identify which key fields are missing and what impact they'd have.
 */
export function identifyMissingFields(input: MemoInput): MissingField[] {
  const missing: MissingField[] = [];

  const isPresent = (value: unknown): boolean => value !== null && value !== undefined;

  const hasText = input.textScores !== null && Object.keys(input.textScores).length > 0;
  const hasRevenue =
    isPresent(input.extractedFacts?.revenue) ||
    isPresent(input.features?.revenue_at_raise);
  const hasFundingTarget =
    isPresent(input.extractedFacts?.fundingTarget) ||
    isPresent(input.features?.funding_target);
  const hasFinancials = isPresent(input.features?.total_assets);
  const hasGrowth = isPresent(input.extractedFacts?.revenueGrowthYoy);
  const instrument = String(input.features?.instrument_type ?? "").toLowerCase();
  const termsRequireCap = instrument.includes("safe") || instrument.includes("convertible");
  const hasValuationCap = isPresent(input.features?.valuation_cap);
  const hasDiscount = isPresent(input.features?.discount_rate);
  const hasLiquidationPref = isPresent(input.features?.liquidation_preference_multiple);

  if (!hasText) {
    missing.push({
      field: "pitchText",
      label: "Pitch text or website URL",
      impact: "Unlocks AI text analysis (20% of score weight). Currently using baseline score of 35.",
    });
  }

  if (!hasRevenue) {
    missing.push({
      field: "revenue",
      label: "Revenue figure",
      impact: "Required for Traction & Growth scoring (18% of weight). Pre-revenue companies capped at 30.",
    });
  }

  if (!hasFundingTarget) {
    missing.push({
      field: "fundingTarget",
      label: "Funding target",
      impact: "Needed for Deal Terms assessment (15% of weight) and overfunding ratio calculation.",
    });
  }

  if (!hasFinancials) {
    missing.push({
      field: "financials",
      label: "Financial statements (assets, debt, cash)",
      impact: "Needed for Financial Health category (12% of weight). Currently using baseline.",
    });
  }

  if (!hasGrowth) {
    missing.push({
      field: "revenueGrowthYoy",
      label: "Revenue growth rate",
      impact: "Strong growth (>50% YoY) adds up to 20 points to Traction & Growth score.",
    });
  }

  if (termsRequireCap && !hasValuationCap) {
    missing.push({
      field: "valuationCap",
      label: "Valuation cap (SAFE/convertible)",
      impact: "Missing cap weakens term-sheet quality and increases downside uncertainty.",
    });
  }

  if (termsRequireCap && !hasDiscount) {
    missing.push({
      field: "discountRate",
      label: "Discount rate (SAFE/convertible)",
      impact: "Discount materially affects conversion economics and investor ownership.",
    });
  }

  if (!hasLiquidationPref) {
    missing.push({
      field: "liquidationPreference",
      label: "Liquidation preference terms",
      impact: "Preference stack drives downside recovery and true return profile.",
    });
  }

  return missing;
}

export function buildDiligenceChecklist(input: MemoInput): string[] {
  const checklist: string[] = [];

  const weakest = Object.entries(input.categories)
    .sort((a, b) => a[1] - b[1])
    .slice(0, 3);
  for (const [category, score] of weakest) {
    if (score < 65) {
      checklist.push(`Validate ${category.toLowerCase()} with primary-source evidence.`);
    }
  }

  for (const missing of identifyMissingFields(input).slice(0, 3)) {
    checklist.push(`Collect missing input: ${missing.label}.`);
  }

  if (input.failedGates.some((g) => g.name.toLowerCase().includes("confidence"))) {
    checklist.push("Run full deep diligence before any capital decision.");
  }

  if (checklist.length === 0) {
    checklist.push("Pressure-test founder claims against customer/reference evidence.");
  }

  return checklist.slice(0, 5);
}

/**
 * Generate an IC memo using Claude Haiku.
 *
 * Returns null if the API call or parsing fails — the caller should
 * fall back to showing scores without a memo.
 */
export async function generateMemo(
  apiKey: string,
  input: MemoInput,
): Promise<ICMemo | null> {
  const client = new Anthropic({ apiKey });

  const prompt = buildMemoPrompt(input);

  const response = await client.messages.create({
    model: "claude-haiku-4-5-20251001",
    max_tokens: 1024,
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

    if (
      typeof parsed.thesis !== "string" ||
      !Array.isArray(parsed.evidence) ||
      !Array.isArray(parsed.risks) ||
      typeof parsed.verdict !== "string"
    ) {
      return null;
    }

    const missingData = identifyMissingFields(input);
    const diligenceChecklist = buildDiligenceChecklist(input);

    return {
      thesis: parsed.thesis,
      evidence: parsed.evidence.filter((e: unknown) => typeof e === "string"),
      risks: parsed.risks.filter((r: unknown) => typeof r === "string"),
      missingData,
      diligenceChecklist,
      verdict: parsed.verdict,
    };
  } catch {
    return null;
  }
}
