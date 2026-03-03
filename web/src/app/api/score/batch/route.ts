import { NextRequest, NextResponse } from "next/server";

import { findCompany, loadFeatures } from "@/lib/db/supabase";
import { resolveRouteContext } from "@/lib/auth/request-context";
import { type CompanyFeatures, type ExportedModel, predict } from "@/lib/scoring/inference";
import { checkGates } from "@/lib/scoring/gates";
import { classify } from "@/lib/scoring/recommendation";
import { computeRubric } from "@/lib/scoring/rubric";
import modelJson from "@/../public/model/model.json";

const MODEL = modelJson as unknown as ExportedModel;

interface BatchItem {
  companyName: string;
  sector?: string;
}

export async function POST(request: NextRequest) {
  const context = await resolveRouteContext(request);
  if (context instanceof NextResponse) {
    return context;
  }
  const body = (await request.json()) as { deals: BatchItem[] };
  const deals = body.deals ?? [];
  if (deals.length === 0) {
    return NextResponse.json({ error: "deals is required" }, { status: 400 });
  }
  const supabase = context.supabase;
  const output: Array<{
    companyName: string;
    matchedCompany: string | null;
    score: number;
    recommendation: string;
    confidenceRange: number;
    dataCompleteness: number;
  }> = [];

  for (const deal of deals.slice(0, 50)) {
    const company = await findCompany(supabase, deal.companyName);
    let features: CompanyFeatures = {};
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
          platform: featureRow.platform ?? null,
          country: featureRow.country ?? company.country,
        };
      }
    }

    let mlScore = 35;
    try {
      mlScore = predict(MODEL, features).score;
    } catch {
      mlScore = 35;
    }
    const rubric = computeRubric({
      mlScore,
      textScore: null,
      textDimensions: null,
      revenue: (features.revenue_at_raise as number) ?? null,
      preRevenue: !features.revenue_at_raise,
      totalAssets: (features.total_assets as number) ?? null,
      totalDebt: (features.total_debt as number) ?? null,
      cashPosition: (features.cash_position as number) ?? null,
      fundingTarget: (features.funding_target as number) ?? null,
      amountRaised: (features.amount_raised as number) ?? null,
      overfundingRatio: (features.overfunding_ratio as number) ?? null,
      hasInstitutionalCoinvestor: false,
      sector: deal.sector ?? company?.sector ?? null,
      revenueGrowthYoy: null,
      preMoneyValuation: null,
      instrumentType: (features.instrument_type as string) ?? null,
      investorCount: null,
      fundingVelocityDays: null,
      valuationContext: null,
    });
    const gates = checkGates({
      dataCompleteness: rubric.dataCompleteness,
      modelScore: rubric.overallScore,
      confidenceRange: rubric.confidenceRange,
      isQuickScore: true,
      enforceReliabilityGates: false,
    });
    const rec = classify(rubric.overallScore, rubric.confidenceRange, gates);

    output.push({
      companyName: deal.companyName,
      matchedCompany: company?.name ?? null,
      score: rubric.overallScore,
      recommendation: rec.label,
      confidenceRange: rubric.confidenceRange,
      dataCompleteness: Math.round(rubric.dataCompleteness * 100),
    });
  }

  output.sort((a, b) => b.score - a.score);
  return NextResponse.json({ results: output });
}
