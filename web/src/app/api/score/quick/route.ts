/**
 * Quick Score API endpoint.
 *
 * Accepts company data, runs ML inference + optional Claude text scoring,
 * applies the rubric + gates, and returns a recommendation.
 */

import { NextRequest, NextResponse } from "next/server";

import { buildProfile, scoreText, type ExtractedFacts } from "@/lib/claude/text-scorer";
import { generateMemo, identifyMissingFields, type ICMemo, type MissingField } from "@/lib/claude/memo-generator";
import { findCompany, getSupabaseClient, loadFeatures, loadDealTerms, loadRegulatoryData, type DealTermsRow } from "@/lib/db/supabase";
import { scrapeWebsite } from "@/lib/enrichment/website-scraper";
import { scoreFromKnowledge } from "@/lib/enrichment/knowledge-enrichment";
import { type CompanyFeatures, type ExportedModel, predict } from "@/lib/scoring/inference";
import { findComparables, type ComparablesResult } from "@/lib/scoring/comparables";
import { checkGates, type GateCheckInput } from "@/lib/scoring/gates";
import { classify } from "@/lib/scoring/recommendation";
import { type RubricInput, computeRubric } from "@/lib/scoring/rubric";
import modelJson from "@/../public/model/model.json";

const MODEL = modelJson as unknown as ExportedModel;

interface QuickScoreRequest {
  companyName: string;
  websiteUrl?: string;
  sector?: string;
  revenue?: number;
  fundingTarget?: number;
  pitchText?: string;
}

interface QuickScoreResponse {
  score: number;
  confidenceRange: number;
  recommendation: {
    class: string;
    label: string;
    description: string;
  };
  categories: Record<string, number>;
  dataCompleteness: number;
  textScores: Record<string, number> | null;
  extractedFacts: ExtractedFacts | null;
  gates: Array<{
    name: string;
    passed: boolean;
    action: string;
    reason: string;
  }>;
  matchedCompany: string | null;
  dataSource: "user" | "website" | "ai_knowledge" | "none";
  generatedProfile: string | null;
  memo: ICMemo | null;
  missingFields: MissingField[];
  comparables: ComparablesResult | null;
  dealTerms: DealTermsRow | null;
  regulatoryStatus: {
    companyStatus: string | null;
    companyNumber: string | null;
  } | null;
}

export async function POST(request: NextRequest) {
  const body = (await request.json()) as QuickScoreRequest;

  if (!body.companyName?.trim()) {
    return NextResponse.json(
      { error: "companyName is required" },
      { status: 400 },
    );
  }

  const supabaseUrl = process.env.SUPABASE_URL;
  const supabaseKey = process.env.SUPABASE_ANON_KEY;
  const anthropicKey = process.env.ANTHROPIC_API_KEY;

  // Step 1: Entity match — look up company in database (optional)
  let company = null;
  let features: CompanyFeatures = {};
  let stageBucket: string | null = null;
  let dealTerms: DealTermsRow | null = null;
  let regulatoryStatus: { companyStatus: string | null; companyNumber: string | null } | null = null;

  if (supabaseUrl && supabaseKey) {
    const supabase = getSupabaseClient(supabaseUrl, supabaseKey);
    company = await findCompany(supabase, body.companyName);

    if (company?.id) {
      try {
        dealTerms = await loadDealTerms(supabase, company.id);
      } catch {
        // Deal terms query failed — continue without
      }
    }

    if (company?.entity_id) {
      // Load regulatory data from Companies House records
      try {
        const regData = await loadRegulatoryData(supabase, company.entity_id);
        if (regData) {
          regulatoryStatus = {
            companyStatus: regData.current_status,
            companyNumber: regData.source_id,
          };
        }
      } catch {
        // Regulatory query failed — continue without
      }

      const featureRow = await loadFeatures(supabase, company.entity_id);
      if (featureRow) {
        stageBucket = featureRow.stage_bucket ?? null;
        features = {
          company_age_months: featureRow.company_age_months,
          employee_count: featureRow.employee_count,
          revenue_at_raise: featureRow.revenue_at_raise,
          pre_revenue: featureRow.pre_revenue ? 1 : 0,
          total_assets: featureRow.total_assets,
          total_debt: featureRow.total_debt,
          debt_to_asset_ratio: featureRow.debt_to_asset_ratio,
          cash_position: featureRow.cash_position,
          funding_target: featureRow.funding_target,
          amount_raised: featureRow.amount_raised,
          overfunding_ratio: featureRow.overfunding_ratio,
          instrument_type: featureRow.instrument_type,
          platform: featureRow.platform ?? null,
          country: featureRow.country ?? company.country,
        };
      }
    }
  }

  // Override with user-provided values
  if (body.revenue !== undefined) features.revenue_at_raise = body.revenue;
  if (body.fundingTarget !== undefined) features.funding_target = body.fundingTarget;

  // Step 2: ML inference
  let mlScore = 35; // Sceptical baseline if no model

  // Step 3: Resolve pitch text source and run Claude scoring
  let textResult: Awaited<ReturnType<typeof scoreText>> = null;
  let dataSource: "user" | "website" | "ai_knowledge" | "none" = "none";
  let generatedProfile: string | null = null;

  if (body.pitchText?.trim() && anthropicKey) {
    // Priority 1: User-provided pitch text
    dataSource = "user";
    const profile = buildProfile({
      name: body.companyName,
      sector: body.sector,
      revenue: body.revenue,
      fundingTarget: body.fundingTarget,
      pitchText: body.pitchText,
    });
    try {
      textResult = await scoreText(anthropicKey, profile);
    } catch {
      // Claude scoring failed — continue
    }
  } else if (body.websiteUrl?.trim() && anthropicKey) {
    // Priority 2: Website scrape
    try {
      const scrapeResult = await scrapeWebsite(body.websiteUrl);
      if (scrapeResult.ok && scrapeResult.text) {
        dataSource = "website";
        const profile = buildProfile({
          name: body.companyName,
          sector: body.sector,
          revenue: body.revenue,
          fundingTarget: body.fundingTarget,
          pitchText: scrapeResult.text,
        });
        textResult = await scoreText(anthropicKey, profile);
      }
    } catch {
      // Scrape or scoring failed — fall through to knowledge
    }

    // If scrape failed, try Claude knowledge
    if (!textResult) {
      try {
        const knowledgeResult = await scoreFromKnowledge(
          anthropicKey, body.companyName, body.sector, body.revenue, body.fundingTarget,
        );
        if (knowledgeResult && !knowledgeResult.unknown) {
          dataSource = "ai_knowledge";
          textResult = knowledgeResult;
          generatedProfile = knowledgeResult.generatedProfile;
        }
      } catch {
        // All enrichment failed
      }
    }
  } else if (anthropicKey) {
    // Priority 3: Claude knowledge enrichment (no pitch text, no website)
    try {
      const knowledgeResult = await scoreFromKnowledge(
        anthropicKey, body.companyName, body.sector, body.revenue, body.fundingTarget,
      );
      if (knowledgeResult && !knowledgeResult.unknown) {
        dataSource = "ai_knowledge";
        textResult = knowledgeResult;
        generatedProfile = knowledgeResult.generatedProfile;
      }
    } catch {
      // Knowledge enrichment failed
    }
  }

  const textScores = textResult?.scores ?? null;
  const extractedFacts = textResult?.extractedFacts ?? null;

  // Step 3b: Fill missing fields from extracted facts
  // Extracted facts only override when the user didn't provide a value
  if (extractedFacts) {
    if (body.revenue === undefined && extractedFacts.revenue !== null) {
      features.revenue_at_raise = extractedFacts.revenue;
    }
    if (body.fundingTarget === undefined && extractedFacts.fundingTarget !== null) {
      features.funding_target = extractedFacts.fundingTarget;
    }
    if (extractedFacts.employeeCount !== null && !features.employee_count) {
      features.employee_count = extractedFacts.employeeCount;
    }
    if (extractedFacts.companyAgeMonths !== null && !features.company_age_months) {
      features.company_age_months = extractedFacts.companyAgeMonths;
    }
  }

  // Step 3c: Re-run ML inference after enrichment so extracted facts can
  // influence the model score when user/DB fields were missing.
  try {
    const result = predict(MODEL, features);
    mlScore = result.score;
  } catch {
    // Model inference failed — use baseline
  }

  // Step 4: Rubric scoring
  const effectiveRevenue = (features.revenue_at_raise as number) ?? body.revenue ?? null;
  const rubricInput: RubricInput = {
    mlScore,
    textScore: textScores?.text_quality_score ?? null,
    textDimensions: textScores
      ? {
          clarity: textScores.clarity,
          claims_plausibility: textScores.claims_plausibility,
          problem_specificity: textScores.problem_specificity,
          differentiation_depth: textScores.differentiation_depth,
          founder_domain_signal: textScores.founder_domain_signal,
          risk_honesty: textScores.risk_honesty,
          business_model_clarity: textScores.business_model_clarity,
        }
      : null,
    revenue: effectiveRevenue,
    preRevenue: !effectiveRevenue,
    totalAssets: (features.total_assets as number) ?? null,
    totalDebt: (features.total_debt as number) ?? null,
    cashPosition: (features.cash_position as number) ?? null,
    fundingTarget: (features.funding_target as number) ?? body.fundingTarget ?? null,
    amountRaised: (features.amount_raised as number) ?? null,
    overfundingRatio: (features.overfunding_ratio as number) ?? null,
    hasInstitutionalCoinvestor: false,
    sector: body.sector ?? null,
    revenueGrowthYoy: extractedFacts?.revenueGrowthYoy ?? null,
  };

  const rubricResult = computeRubric(rubricInput);

  // Step 5: Abstention gates
  const gateInput: GateCheckInput = {
    dataCompleteness: rubricResult.dataCompleteness,
    modelScore: rubricResult.overallScore,
    confidenceRange: rubricResult.confidenceRange,
    isQuickScore: true,
  };

  const gates = checkGates(gateInput);

  // Step 6: Recommendation
  const recommendation = classify(
    rubricResult.overallScore,
    rubricResult.confidenceRange,
    gates,
  );

  // Step 7: IC Memo generation + missing fields analysis
  const failedGates = gates.filter((g) => !g.passed);
  const memoInput = {
    companyName: body.companyName,
    sector: body.sector ?? null,
    matchedCompany: company?.name ?? null,
    score: rubricResult.overallScore,
    confidenceRange: rubricResult.confidenceRange,
    recommendationLabel: recommendation.label,
    recommendationDescription: recommendation.description,
    recommendationClass: recommendation.class,
    categories: {
      "Text & Narrative": rubricResult.categories.textNarrative,
      "Traction & Growth": rubricResult.categories.tractionGrowth,
      "Deal Terms": rubricResult.categories.dealTerms,
      "Team": rubricResult.categories.team,
      "Financial Health": rubricResult.categories.financialHealth,
      "Investment Signal": rubricResult.categories.investmentSignal,
      "Market": rubricResult.categories.market,
    },
    textScores: textScores
      ? Object.fromEntries(
          Object.entries(textScores).map(([k, v]) => [k, v]),
        )
      : null,
    extractedFacts: extractedFacts ?? null,
    features: Object.keys(features).length > 0 ? (features as Record<string, number | string | null>) : null,
    failedGates: failedGates.map((g) => ({ name: g.name, reason: g.reason })),
    dataSource,
    dataCompleteness: Math.round(rubricResult.dataCompleteness * 100),
    generatedProfile,
  };

  const missingFields = identifyMissingFields(memoInput);

  let memo: ICMemo | null = null;
  if (anthropicKey) {
    try {
      memo = await generateMemo(anthropicKey, memoInput);
    } catch {
      // Memo generation failed — continue without it
    }
  }

  // Step 8: Comparables — find similar deals and compute cohort stats
  let comparables: ComparablesResult | null = null;
  if (supabaseUrl && supabaseKey) {
    try {
      const supabase = getSupabaseClient(supabaseUrl, supabaseKey);
      comparables = await findComparables(supabase, {
        sector: body.sector ?? company?.sector ?? null,
        country: (features.country as string) ?? company?.country ?? null,
        stageBucket,
        fundingTarget: (features.funding_target as number) ?? body.fundingTarget ?? null,
        companyAge: (features.company_age_months as number) ?? null,
        revenue: effectiveRevenue,
        excludeCompanyId: company?.id ?? null,
      });
    } catch {
      // Comparables query failed — continue without it
    }
  }

  const response: QuickScoreResponse = {
    score: rubricResult.overallScore,
    confidenceRange: rubricResult.confidenceRange,
    recommendation: {
      class: recommendation.class,
      label: recommendation.label,
      description: recommendation.description,
    },
    categories: {
      "Text & Narrative": rubricResult.categories.textNarrative,
      "Traction & Growth": rubricResult.categories.tractionGrowth,
      "Deal Terms": rubricResult.categories.dealTerms,
      "Team": rubricResult.categories.team,
      "Financial Health": rubricResult.categories.financialHealth,
      "Investment Signal": rubricResult.categories.investmentSignal,
      "Market": rubricResult.categories.market,
    },
    dataCompleteness: Math.round(rubricResult.dataCompleteness * 100),
    textScores: textScores
      ? Object.fromEntries(
          Object.entries(textScores).map(([k, v]) => [k, v]),
        )
      : null,
    extractedFacts: extractedFacts ?? null,
    gates: gates.map((g) => ({
      name: g.name,
      passed: g.passed,
      action: g.action,
      reason: g.reason,
    })),
    matchedCompany: company?.name ?? null,
    dataSource,
    generatedProfile,
    memo,
    missingFields,
    comparables,
    dealTerms,
    regulatoryStatus,
  };

  return NextResponse.json(response);
}
