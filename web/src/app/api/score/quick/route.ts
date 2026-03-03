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
import { canConsumeUsage, recordUsageEvent } from "@/lib/auth/usage-limits";
import { screenNameAgainstSanctions } from "@/lib/compliance/sanctions";
import {
  findCompany,
  loadFeatures,
  loadDealTerms,
  loadFundingHistory,
  loadFeatureProvenance,
  loadRegulatoryData,
  loadSegmentEvidence,
  insertValuationScenarioAudit,
  insertSanctionsScreening,
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
import { deriveSegmentKey } from "@/lib/scoring/segments";
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
    confidenceBand: "low" | "medium" | "high";
    auditedAgainstRealized: boolean;
    notes: string[];
  } | null;
  valuationConfidence: "low" | "medium" | "high";
  valuationConfidenceReason: string;
  segmentEvidence: {
    segmentKey: string;
    sampleSize: number;
    survivalAuc: number | null;
    calibrationEce: number | null;
    releaseGateOpen: boolean;
    evidenceOk: boolean;
    lastBacktestDate: string | null;
  } | null;
  sanctions: {
    checked: boolean;
    matched: boolean;
    riskLevel: "clear" | "potential_match";
    matchSource: string | null;
    matchName: string | null;
    reason: string;
  };
  regulatoryStatus: {
    companyStatus: string | null;
    companyNumber: string | null;
  } | null;
  usage: {
    planLimit: number;
    usedThisMonth: number;
    remaining: number;
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

  const usageGate = await canConsumeUsage(supabase, context.actorEmail, "quick_score", 1);
  if (!usageGate.allowed) {
    return NextResponse.json(
      {
        error: usageGate.reason,
        usage: {
          planLimit: usageGate.limit,
          usedThisMonth: usageGate.used,
          remaining: Math.max(0, usageGate.limit - usageGate.used),
        },
      },
      { status: 429 },
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
  let segmentEvidence: QuickScoreResponse["segmentEvidence"] = null;
  let sanctions: QuickScoreResponse["sanctions"] = {
    checked: false,
    matched: false,
    riskLevel: "clear",
    matchSource: null,
    matchName: null,
    reason: "Sanctions check not run.",
  };

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

  // Step 3d: Segment evidence + sanctions screen
  const segmentKey = deriveSegmentKey(
    (features.country as string) ?? company?.country ?? null,
    stageBucket,
  );
  try {
    const evidence = await loadSegmentEvidence(supabase, segmentKey);
    if (evidence) {
      const evidenceOk =
        evidence.sample_size >= 200
        && evidence.release_gate_open
        && evidence.survival_auc !== null
        && evidence.survival_auc >= 0.65
        && evidence.calibration_ece !== null
        && evidence.calibration_ece <= 0.10;
      segmentEvidence = {
        segmentKey: evidence.segment_key,
        sampleSize: evidence.sample_size,
        survivalAuc: evidence.survival_auc,
        calibrationEce: evidence.calibration_ece,
        releaseGateOpen: evidence.release_gate_open,
        evidenceOk,
        lastBacktestDate: evidence.last_backtest_date,
      };
    }
  } catch {
    // Segment evidence unavailable — gate will abstain
  }

  if (process.env.ENABLE_SANCTIONS_SCREENING === "true") {
    try {
      sanctions = await screenNameAgainstSanctions(body.companyName);
      await insertSanctionsScreening(supabase, {
        screened_name: body.companyName.trim(),
        normalized_name: body.companyName
          .toLowerCase()
          .replace(/[^a-z0-9]+/g, " ")
          .replace(/\\s+/g, " ")
          .trim(),
        matched: sanctions.matched,
        match_source: sanctions.matchSource,
        match_name: sanctions.matchName,
        risk_level: sanctions.riskLevel,
        details: { reason: sanctions.reason },
      });
    } catch {
      // Screening errors should not break scoring
    }
  } else {
    sanctions = {
      checked: false,
      matched: false,
      riskLevel: "clear",
      matchSource: null,
      matchName: null,
      reason: "Sanctions screening disabled in runtime profile.",
    };
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
    }, {
      liteMode: true,
      maxRows: 900,
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
  const valuationConfidence = comparables?.valuationConfidence ?? "low";
  const valuationConfidenceReason =
    comparables?.valuationConfidenceReason
    ?? "No valuation context was available from current comparables.";
  const valuationScenario = computeValuationScenario({
    stageBucket,
    preMoneyValuation: dealTerms?.pre_money_valuation ?? null,
    revenue: effectiveRevenue,
    valuationSignal: comparables?.valuationContext?.signal ?? null,
    cohortMedianMultiple: comparables?.valuationContext?.cohortMedianMultiple ?? null,
    valuationConfidence,
  });

  // Step 6: Abstention gates
  const gateInput: GateCheckInput = {
    dataCompleteness: rubricResult.dataCompleteness,
    modelScore: rubricResult.overallScore,
    confidenceRange: rubricResult.confidenceRange,
    isQuickScore: true,
    valuationConfidence,
    segmentEvidence: segmentEvidence
      ? {
          segmentKey: segmentEvidence.segmentKey,
          sampleSize: segmentEvidence.sampleSize,
          survivalAuc: segmentEvidence.survivalAuc,
          calibrationEce: segmentEvidence.calibrationEce,
          releaseGateOpen: segmentEvidence.releaseGateOpen,
        }
      : null,
    sanctionsMatch: sanctions.matched,
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
    valuationConfidence,
    valuationConfidenceReason,
    segmentEvidence,
    provenanceSummary: provenance
      ? {
          newestAsOfDate: provenance.newestAsOfDate,
          stale: provenance.stale,
          fieldCount: provenance.fields.length,
        }
      : null,
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

  try {
    await insertValuationScenarioAudit(supabase, {
      company_id: company?.id ?? null,
      entity_id: company?.entity_id ?? null,
      evaluation_type: "quick",
      segment_key: segmentKey,
      recommendation_class: recommendation.class,
      score: rubricResult.overallScore,
      data_completeness: rubricResult.dataCompleteness,
      valuation_confidence: valuationConfidence,
      valuation_confidence_reason: valuationConfidenceReason,
      valuation_source_summary: comparables?.sourceSummary ?? null,
      entry_multiple: valuationScenario?.entryMultiple ?? null,
      bear_moic: valuationScenario?.bearMoic ?? null,
      base_moic: valuationScenario?.baseMoic ?? null,
      bull_moic: valuationScenario?.bullMoic ?? null,
    });
  } catch {
    // Audit logging failures should not block scoring
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
    valuationConfidence,
    valuationConfidenceReason,
    segmentEvidence,
    sanctions,
    regulatoryStatus,
    usage: {
      planLimit: usageGate.limit,
      usedThisMonth: usageGate.used + 1,
      remaining: Math.max(0, usageGate.limit - (usageGate.used + 1)),
    },
  };

  void recordUsageEvent(supabase, context.actorEmail, "quick_score", 1, {
    companyName: body.companyName,
    matchedCompany: company?.name ?? null,
    recommendationClass: recommendation.class,
    score: rubricResult.overallScore,
  });

  return NextResponse.json(response);
}
