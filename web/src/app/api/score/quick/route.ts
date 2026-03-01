/**
 * Quick Score API endpoint.
 *
 * Accepts company data, runs ML inference + optional Claude text scoring,
 * applies the rubric + gates, and returns a recommendation.
 */

import { NextRequest, NextResponse } from "next/server";

import { buildProfile, scoreText } from "@/lib/claude/text-scorer";
import { findCompany, getSupabaseClient, loadFeatures } from "@/lib/db/supabase";
import { type CompanyFeatures, type ExportedModel, predict } from "@/lib/scoring/inference";
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
  gates: Array<{
    name: string;
    passed: boolean;
    action: string;
    reason: string;
  }>;
  matchedCompany: string | null;
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

  if (supabaseUrl && supabaseKey) {
    const supabase = getSupabaseClient(supabaseUrl, supabaseKey);
    company = await findCompany(supabase, body.companyName);

    if (company?.entity_id) {
      const featureRow = await loadFeatures(supabase, company.entity_id);
      if (featureRow) {
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
          platform: featureRow.platform ?? company.platform,
          country: featureRow.country ?? company.country,
        };
      }
    }
  }

  // Override with user-provided values
  if (body.revenue !== undefined) features.revenue_at_raise = body.revenue;
  if (body.fundingTarget !== undefined) features.funding_target = body.fundingTarget;
  if (body.sector) features.platform = undefined; // Use sector from input

  // Step 2: ML inference
  let mlScore = 35; // Sceptical baseline if no model

  try {
    const result = predict(MODEL, features);
    mlScore = result.score;
  } catch {
    // Model inference failed — use baseline
  }

  // Step 3: Claude text scoring (if pitch text provided and API key available)
  let textScores = null;
  if (body.pitchText?.trim() && anthropicKey) {
    const profile = buildProfile({
      name: body.companyName,
      sector: body.sector,
      revenue: body.revenue,
      fundingTarget: body.fundingTarget,
      pitchText: body.pitchText,
    });

    try {
      textScores = await scoreText(anthropicKey, profile);
    } catch {
      // Claude scoring failed — continue without text scores
    }
  }

  // Step 4: Rubric scoring
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
    revenue: (features.revenue_at_raise as number) ?? body.revenue ?? null,
    preRevenue: !features.revenue_at_raise && !body.revenue,
    totalAssets: (features.total_assets as number) ?? null,
    totalDebt: (features.total_debt as number) ?? null,
    cashPosition: (features.cash_position as number) ?? null,
    fundingTarget: (features.funding_target as number) ?? body.fundingTarget ?? null,
    amountRaised: (features.amount_raised as number) ?? null,
    overfundingRatio: (features.overfunding_ratio as number) ?? null,
    hasInstitutionalCoinvestor: false,
    sector: body.sector ?? null,
    revenueGrowthYoy: null,
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
    gates: gates.map((g) => ({
      name: g.name,
      passed: g.passed,
      action: g.action,
      reason: g.reason,
    })),
    matchedCompany: company?.name ?? null,
  };

  return NextResponse.json(response);
}
