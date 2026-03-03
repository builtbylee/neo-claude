/**
 * Quick Score API endpoint.
 *
 * Accepts company data, runs ML inference + optional Claude text scoring,
 * applies the rubric + gates, and returns a recommendation.
 */

import { NextRequest, NextResponse } from "next/server";

import { buildProfile, scoreText, type ExtractedFacts } from "@/lib/claude/text-scorer";
import { generateMemo, identifyMissingFields, type ICMemo, type MissingField } from "@/lib/claude/memo-generator";
import { resolveRouteContext } from "@/lib/auth/request-context";
import {
  findCompany,
  loadFeatures,
  loadDealTerms,
  loadFundingHistory,
  loadFeatureProvenance,
  loadRegulatoryData,
  type DealTermsRow,
  type FundingRoundRow,
} from "@/lib/db/supabase";
import { ingestDocuments, type UploadedDocument } from "@/lib/enrichment/document-ingestion";
import { scrapeWebsite } from "@/lib/enrichment/website-scraper";
import { scoreFromKnowledge } from "@/lib/enrichment/knowledge-enrichment";
import { type CompanyFeatures, type ExportedModel, predict } from "@/lib/scoring/inference";
import { findComparables, type ComparablesResult } from "@/lib/scoring/comparables";
import { checkGates, type GateCheckInput } from "@/lib/scoring/gates";
import { classify } from "@/lib/scoring/recommendation";
import { type RubricInput, computeRubric } from "@/lib/scoring/rubric";
import { computeValuationScenario } from "@/lib/scoring/valuation";
import modelJson from "@/../public/model/model.json";

const MODEL = modelJson as unknown as ExportedModel;

interface QuickScoreRequest {
  companyName: string;
  websiteUrl?: string;
  sector?: string;
  revenue?: number;
  fundingTarget?: number;
  pitchText?: string;
  documents?: UploadedDocument[];
}

interface QuickScoreResponse {
  score: number;
  confidenceRange: number;
  recommendation: {
    class: string;
    originalClass: string;
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
  dataSource: "user" | "document" | "website" | "ai_knowledge" | "none";
  generatedProfile: string | null;
  memo: ICMemo | null;
  missingFields: MissingField[];
  comparables: ComparablesResult | null;
  dealTerms: DealTermsRow | null;
  fundingHistory: FundingRoundRow[];
  assessmentWarning: string | null;
  documentSummary: {
    parsed: Array<{ name: string; mimeType: string; extractedChars: number }>;
    warnings: string[];
  };
  provenance: {
    newestAsOfDate: string | null;
    stale: boolean;
    fields: Array<{
      feature: string;
      source: string;
      asOfDate: string;
      stalenessDays: number;
    }>;
  } | null;
  valuationScenario: {
    entryMultiple: number;
    cohortMedianMultiple: number | null;
    dilutionRetention: number;
    bearMoic: number;
    baseMoic: number;
    bullMoic: number;
    notes: string[];
  } | null;
  regulatoryStatus: {
    companyStatus: string | null;
    companyNumber: string | null;
  } | null;
}

export async function POST(request: NextRequest) {
  const context = await resolveRouteContext(request);
  if (context instanceof NextResponse) {
    return context;
  }
  const { supabase } = context;
  const body = (await request.json()) as QuickScoreRequest;

  if (!body.companyName?.trim()) {
    return NextResponse.json(
      { error: "companyName is required" },
      { status: 400 },
    );
  }

  const anthropicKey = process.env.ANTHROPIC_API_KEY;

  // Step 1: Entity match — look up company in database (optional)
  let company = null;
  let features: CompanyFeatures = {};
  let stageBucket: string | null = null;
  let dealTerms: DealTermsRow | null = null;
  let fundingHistory: FundingRoundRow[] = [];
  let provenance: QuickScoreResponse["provenance"] = null;
  let regulatoryStatus: { companyStatus: string | null; companyNumber: string | null } | null = null;

  {
    company = await findCompany(supabase, body.companyName);

    if (company?.id) {
      try {
        dealTerms = await loadDealTerms(supabase, company.id);
        fundingHistory = await loadFundingHistory(supabase, company.id);
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

      try {
        const provenanceRows = await loadFeatureProvenance(supabase, company.entity_id, [
          "revenue_at_raise",
          "funding_target",
          "company_age_months",
          "employee_count",
          "total_assets",
          "total_debt",
          "overfunding_ratio",
        ]);

        if (provenanceRows.length > 0) {
          const now = new Date();
          const fields = provenanceRows.map((row) => {
            const asOfDate = row.as_of_date;
            const ageMs = now.getTime() - new Date(asOfDate).getTime();
            const stalenessDays = Math.max(0, Math.floor(ageMs / (24 * 60 * 60 * 1000)));
            return {
              feature: row.feature_name,
              source: row.source,
              asOfDate,
              stalenessDays,
            };
          });
          const newestAsOfDate = fields
            .map((f) => f.asOfDate)
            .sort((a, b) => (a > b ? -1 : 1))[0] ?? null;
          provenance = {
            newestAsOfDate,
            stale: fields.every((f) => f.stalenessDays > 365),
            fields,
          };
        }
      } catch {
        // Provenance query failed — continue without
      }
    }
  }

  // Override with user-provided values
  if (body.revenue !== undefined) features.revenue_at_raise = body.revenue;
  if (body.fundingTarget !== undefined) features.funding_target = body.fundingTarget;

  // Step 2: ML inference
  let mlScore = 35; // Sceptical baseline if no model

  // Step 3: Prepare optional uploaded docs
  const documentIngestion = await ingestDocuments(body.documents);
  const documentsText = documentIngestion.combinedText;

  // Step 4: Resolve pitch text source and run Claude scoring
  let textResult: Awaited<ReturnType<typeof scoreText>> = null;
  let dataSource: "user" | "document" | "website" | "ai_knowledge" | "none" = "none";
  let generatedProfile: string | null = null;
  const mergedPitchText = [body.pitchText?.trim(), documentsText]
    .filter((v) => Boolean(v))
    .join("\n\n");

  if (mergedPitchText && anthropicKey) {
    // Priority 1: user text and/or uploaded documents
    dataSource = body.pitchText?.trim() ? "user" : "document";
    const profile = buildProfile({
      name: body.companyName,
      sector: body.sector,
      revenue: body.revenue,
      fundingTarget: body.fundingTarget,
      pitchText: mergedPitchText,
    });
    try {
      textResult = await scoreText(anthropicKey, profile, {
        pdfDocuments: documentIngestion.pdfDocuments,
      });
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
        textResult = await scoreText(anthropicKey, profile, {
          pdfDocuments: documentIngestion.pdfDocuments,
        });
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

  // Step 4: Comparables — used for valuation context and final output
  const effectiveRevenue = (features.revenue_at_raise as number) ?? body.revenue ?? null;
  let comparables: ComparablesResult | null = null;
  try {
    comparables = await findComparables(supabase, {
      sector: body.sector ?? company?.sector ?? null,
      country: (features.country as string) ?? company?.country ?? null,
      stageBucket,
      fundingTarget: (features.funding_target as number) ?? body.fundingTarget ?? null,
      preMoneyValuation: dealTerms?.pre_money_valuation ?? null,
      companyAge: (features.company_age_months as number) ?? null,
      revenue: effectiveRevenue,
      excludeCompanyId: company?.id ?? null,
    });
  } catch {
    // Comparables query failed — continue without it
  }

  // Step 5: Rubric scoring
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
    amountRaised: (features.amount_raised as number) ?? dealTerms?.amount_raised ?? null,
    overfundingRatio: (features.overfunding_ratio as number) ?? dealTerms?.overfunding_ratio ?? null,
    hasInstitutionalCoinvestor: Boolean(dealTerms?.qualified_institutional),
    sector: body.sector ?? company?.sector ?? null,
    revenueGrowthYoy: extractedFacts?.revenueGrowthYoy ?? null,
    preMoneyValuation: dealTerms?.pre_money_valuation ?? null,
    instrumentType: dealTerms?.instrument_type ?? (features.instrument_type as string) ?? null,
    investorCount: dealTerms?.investor_count ?? null,
    fundingVelocityDays: dealTerms?.funding_velocity_days ?? null,
    valuationContext: comparables?.valuationContext
      ? {
          valuationPercentile: comparables.valuationContext.valuationPercentile,
          impliedRevenueMultiple: comparables.valuationContext.impliedRevenueMultiple,
          cohortMedianMultiple: comparables.valuationContext.cohortMedianMultiple,
          signal: comparables.valuationContext.signal,
        }
      : null,
  };

  const rubricResult = computeRubric(rubricInput);
  const valuationScenario = computeValuationScenario({
    stageBucket,
    preMoneyValuation: dealTerms?.pre_money_valuation ?? null,
    revenue: effectiveRevenue,
    valuationSignal: comparables?.valuationContext?.signal ?? null,
    cohortMedianMultiple: comparables?.valuationContext?.cohortMedianMultiple ?? null,
  });

  // Step 6: Abstention gates
  const gateInput: GateCheckInput = {
    dataCompleteness: rubricResult.dataCompleteness,
    modelScore: rubricResult.overallScore,
    confidenceRange: rubricResult.confidenceRange,
    isQuickScore: true,
  };

  const gates = checkGates(gateInput);

  // Step 7: Recommendation
  let recommendation = classify(
    rubricResult.overallScore,
    rubricResult.confidenceRange,
    gates,
  );
  const originalRecommendationClass = recommendation.class;
  let assessmentWarning: string | null = null;

  // User-selected policy: route abstentions to Deep Diligence with explicit warning.
  if (recommendation.class === "abstain") {
    recommendation = {
      ...recommendation,
      class: "deep_diligence",
      label: "Deep Diligence (Low Confidence)",
      description: "Evidence is incomplete. Do not invest yet; run deeper diligence first.",
    };
    assessmentWarning =
      "Model confidence is insufficient for a reliable recommendation. Treated as Deep Diligence by policy.";
  }

  // Step 8: IC Memo generation + missing fields analysis
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

  const response: QuickScoreResponse = {
    score: rubricResult.overallScore,
    confidenceRange: rubricResult.confidenceRange,
    recommendation: {
      class: recommendation.class,
      originalClass: originalRecommendationClass,
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
    fundingHistory,
    assessmentWarning,
    documentSummary: {
      parsed: documentIngestion.parsedDocuments,
      warnings: documentIngestion.warnings,
    },
    provenance,
    valuationScenario,
    regulatoryStatus,
  };

  return NextResponse.json(response);
}
