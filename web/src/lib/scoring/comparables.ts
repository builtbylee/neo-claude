/**
 * Comparables engine — combines:
 * 1) outcome cohorts (crowdfunding_outcomes) for survival/failure base rates
 * 2) broad pricing cohorts (training_features_wide + funding_rounds) for valuation context
 *
 * This improves valuation context breadth with free/public data only.
 */

import { type SupabaseClient } from "@supabase/supabase-js";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnySupabaseClient = SupabaseClient<any, any, any>;

export interface CohortStats {
  sampleSize: number;
  failureRate: number;
  survivalRate: number;
  exitRate: number;
  medianPreMoneyValuation: number | null;
  medianRevenueMultiple: number | null;
  medianFundingTarget: number | null;
  medianRevenueAtRaise: number | null;
  medianCompanyAgeMonths: number | null;
  medianOverfundingRatio: number | null;
  pctWithInstitutional: number;
  pctPreRevenue: number;
}

export interface Comparable {
  name: string;
  sector: string | null;
  country: string | null;
  stageBucket: string | null;
  fundingTarget: number | null;
  revenueAtRaise: number | null;
  companyAgeMonths: number | null;
  outcome: string;
  platform: string | null;
  campaignDate: string | null;
}

export interface ValuationContext {
  valuationPercentile: number;
  impliedRevenueMultiple: number;
  cohortMedianMultiple: number;
  signal: "attractive" | "fair" | "aggressive";
  note: string;
  dataSource: "pricing_cohort" | "outcome_cohort";
  sampleSize: number;
  multipleType: "revenue_multiple" | "raise_proxy_multiple";
  sourceTier: "A" | "B" | "C";
}

export interface ComparablesResult {
  cohortStats: CohortStats;
  cohortLabel: string;
  nearestDeals: Comparable[];
  valuationContext: ValuationContext | null;
  sourceSummary: {
    outcomeSampleSize: number;
    pricingSampleSize: number;
    pricingRevenueSampleSize: number;
    pricingProxySampleSize: number;
    pricingStageAlignedSample: number;
    pricingStageCountrySectorSample: number;
    pricingSourceBreakdown: Record<string, number>;
    pricingTierBreakdown: Record<"A" | "B" | "C", number>;
    pricingTierAShare: number;
    officialSignalCount: number;
    officialSignalTypeBreakdown: Record<string, number>;
    weightedPricingCoverage: number;
    confidencePenalty: number;
    confidencePenaltyReasons: string[];
  };
  sourceConfidence: "low" | "medium" | "high";
  valuationConfidence: "low" | "medium" | "high";
  valuationConfidenceReason: string;
}

interface OutcomeCohortRow {
  company_id: string;
  sector: string | null;
  country: string | null;
  stage_bucket: string | null;
  platform: string | null;
  campaign_date: string | null;
  funding_target: number | null;
  amount_raised: number | null;
  overfunding_ratio: number | null;
  pre_money_valuation: number | null;
  company_age_at_raise_months: number | null;
  had_revenue: boolean | null;
  revenue_at_raise: number | null;
  qualified_institutional_coinvestor: boolean | null;
  outcome: string;
  company_name: string | null;
}

interface PricingCohortRow {
  key: string;
  sector: string | null;
  country: string | null;
  stageBucket: string | null;
  pre_money_valuation: number | null;
  revenue_at_raise: number | null;
  amount_raised: number | null;
  source: string;
  sourceTier: "A" | "B" | "C";
  sourceTierWeight: number;
}

interface CohortFilters {
  sector?: string | null;
  country?: string | null;
  stageBucket?: string | null;
}

type OutcomeQueryFn = (
  supabase: AnySupabaseClient,
  filters: CohortFilters,
) => Promise<OutcomeCohortRow[]>;

type PricingQueryFn = (
  supabase: AnySupabaseClient,
  filters: CohortFilters,
) => Promise<PricingCohortRow[]>;

type OfficialSignalQueryFn = (
  supabase: AnySupabaseClient,
  filters: CohortFilters,
) => Promise<Array<{ signal_type: string | null }>>;

const SOURCE_TIER_WEIGHT: Record<"A" | "B" | "C", number> = {
  A: 1.0,
  B: 0.7,
  C: 0.4,
};

function tierForCompanySource(sourceRaw: string | null | undefined): "A" | "B" | "C" {
  const source = (sourceRaw ?? "unknown").toLowerCase();
  if ([
    "sec_edgar",
    "sec_dera_cf",
    "companies_house",
    "sec_cf_filings",
    "transaction_rounds_truth",
  ].includes(source)) {
    return "A";
  }
  if (["sec_form_d", "manual", "academic"].includes(source)) {
    return "B";
  }
  return "C";
}

function downgradeConfidence(value: "low" | "medium" | "high"): "low" | "medium" | "high" {
  if (value === "high") return "medium";
  if (value === "medium") return "low";
  return "low";
}

function normalizeStageBucket(value: string | null | undefined): string | null {
  if (!value) return null;
  const lower = value.toLowerCase();
  if (lower.includes("seed")) return "seed";
  if (lower.includes("early")) return "early_growth";
  if (lower.includes("series a")) return "early_growth";
  if (lower.includes("series b")) return "early_growth";
  if (lower.includes("a") && lower.includes("series")) return "early_growth";
  if (lower.includes("b") && lower.includes("series")) return "early_growth";
  return null;
}

function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0
    ? (sorted[mid - 1] + sorted[mid]) / 2
    : sorted[mid];
}

function clamp01(value: number): number {
  return Math.max(0, Math.min(1, value));
}

function computeStats(rows: OutcomeCohortRow[]): CohortStats {
  const total = rows.length;
  if (total === 0) {
    return {
      sampleSize: 0,
      failureRate: 0,
      survivalRate: 0,
      exitRate: 0,
      medianPreMoneyValuation: null,
      medianRevenueMultiple: null,
      medianFundingTarget: null,
      medianRevenueAtRaise: null,
      medianCompanyAgeMonths: null,
      medianOverfundingRatio: null,
      pctWithInstitutional: 0,
      pctPreRevenue: 0,
    };
  }

  const failed = rows.filter((r) => r.outcome === "failed").length;
  const trading = rows.filter((r) => r.outcome === "trading").length;
  const exited = rows.filter((r) => r.outcome === "exited").length;

  const fundingTargets = rows
    .map((r) => r.funding_target)
    .filter((v): v is number => v !== null && v > 0);
  const revenues = rows
    .map((r) => r.revenue_at_raise)
    .filter((v): v is number => v !== null && v > 0);
  const ages = rows
    .map((r) => r.company_age_at_raise_months)
    .filter((v): v is number => v !== null && v > 0);
  const overfunding = rows
    .map((r) => r.overfunding_ratio)
    .filter((v): v is number => v !== null && v > 0);
  const preMoneyVals = rows
    .map((r) => r.pre_money_valuation)
    .filter((v): v is number => v !== null && v > 0);
  const revenueMultiples = rows
    .map((r) => {
      if (!r.pre_money_valuation || !r.revenue_at_raise || r.revenue_at_raise <= 0) {
        return null;
      }
      return r.pre_money_valuation / r.revenue_at_raise;
    })
    .filter((v): v is number => v !== null && isFinite(v) && v > 0);

  const institutional = rows.filter(
    (r) => r.qualified_institutional_coinvestor === true,
  ).length;
  const preRevenue = rows.filter(
    (r) => r.had_revenue === false || r.revenue_at_raise === null || r.revenue_at_raise === 0,
  ).length;

  return {
    sampleSize: total,
    failureRate: clamp01(failed / total),
    survivalRate: clamp01(trading / total),
    exitRate: clamp01(exited / total),
    medianPreMoneyValuation: median(preMoneyVals),
    medianRevenueMultiple: median(revenueMultiples),
    medianFundingTarget: median(fundingTargets),
    medianRevenueAtRaise: median(revenues),
    medianCompanyAgeMonths: median(ages),
    medianOverfundingRatio: median(overfunding),
    pctWithInstitutional: clamp01(institutional / total),
    pctPreRevenue: clamp01(preRevenue / total),
  };
}

function buildValuationContext(
  multiples: number[],
  input: { preMoneyValuation?: number | null; revenue?: number | null; fundingTarget?: number | null },
  opts: {
    dataSource: ValuationContext["dataSource"];
    multipleType: ValuationContext["multipleType"];
    sourceTier: ValuationContext["sourceTier"];
  },
): ValuationContext | null {
  if (!input.preMoneyValuation || input.preMoneyValuation <= 0) {
    return null;
  }

  let impliedMultiple: number | null = null;
  if (opts.multipleType === "revenue_multiple") {
    if (!input.revenue || input.revenue <= 0) return null;
    impliedMultiple = input.preMoneyValuation / input.revenue;
  } else {
    if (!input.fundingTarget || input.fundingTarget <= 0) return null;
    impliedMultiple = input.preMoneyValuation / input.fundingTarget;
  }

  if (!impliedMultiple || !isFinite(impliedMultiple) || impliedMultiple <= 0) {
    return null;
  }
  if (multiples.length < 20) return null;

  const sorted = [...multiples].sort((a, b) => a - b);
  const lessOrEqual = sorted.filter((m) => m <= impliedMultiple).length;
  const valuationPercentile = Math.round((lessOrEqual / sorted.length) * 100);
  const cohortMedianMultiple = median(sorted);
  if (cohortMedianMultiple === null) return null;

  let signal: ValuationContext["signal"] = "fair";
  let note = "Pricing is near the cohort midpoint.";
  if (valuationPercentile >= 75) {
    signal = "aggressive";
    note = "Pricing is in the upper quartile vs comparable deals.";
  } else if (valuationPercentile <= 30) {
    signal = "attractive";
    note = "Pricing is in the lower third vs comparable deals.";
  }

  return {
    valuationPercentile,
    impliedRevenueMultiple: Math.round(impliedMultiple * 10) / 10,
    cohortMedianMultiple: Math.round(cohortMedianMultiple * 10) / 10,
    signal,
    note,
    dataSource: opts.dataSource,
    sampleSize: sorted.length,
    multipleType: opts.multipleType,
    sourceTier: opts.sourceTier,
  };
}

function dealDistance(
  target: { fundingTarget?: number | null; companyAge?: number | null; revenue?: number | null },
  row: OutcomeCohortRow,
): number {
  let dist = 0;
  let fields = 0;

  if (target.fundingTarget && row.funding_target && target.fundingTarget > 0) {
    dist += Math.abs(Math.log(row.funding_target / target.fundingTarget));
    fields++;
  }

  if (target.companyAge && row.company_age_at_raise_months && target.companyAge > 0) {
    dist += Math.abs(row.company_age_at_raise_months - target.companyAge) / 60;
    fields++;
  }

  if (target.revenue && row.revenue_at_raise && target.revenue > 0) {
    dist += Math.abs(Math.log((row.revenue_at_raise + 1) / (target.revenue + 1)));
    fields++;
  }

  return fields > 0 ? dist / fields : 999;
}

function inferConfidence(
  outcomeSampleSize: number,
  pricingSampleSize: number,
  tierBreakdown: Record<"A" | "B" | "C", number>,
): "low" | "medium" | "high" {
  const weightedCoverage = pricingSampleSize > 0
    ? (
        tierBreakdown.A * SOURCE_TIER_WEIGHT.A
        + tierBreakdown.B * SOURCE_TIER_WEIGHT.B
        + tierBreakdown.C * SOURCE_TIER_WEIGHT.C
      ) / pricingSampleSize
    : 0;
  if (outcomeSampleSize >= 200 && pricingSampleSize >= 400 && weightedCoverage >= 0.75) {
    return "high";
  }
  if (outcomeSampleSize >= 50 && pricingSampleSize >= 120 && weightedCoverage >= 0.55) {
    return "medium";
  }
  return "low";
}

function inferValuationConfidence(input: {
  valuationContext: ValuationContext | null;
  sourceConfidence: "low" | "medium" | "high";
  pricingRevenueSampleSize: number;
  pricingProxySampleSize: number;
  pricingStageAlignedSample: number;
  pricingStageCountrySectorSample: number;
  tierBreakdown: Record<"A" | "B" | "C", number>;
  weightedCoverage: number;
}): {
  confidence: "low" | "medium" | "high";
  reason: string;
  penalty: number;
  penaltyReasons: string[];
} {
  if (!input.valuationContext) {
    return {
      confidence: "low",
      reason: "Insufficient valuation comparables for a reliable pricing context.",
      penalty: 0,
      penaltyReasons: [],
    };
  }

  const hasRevenueMultiples = input.valuationContext.multipleType === "revenue_multiple";
  let confidence: "low" | "medium" | "high";
  let reason: string;
  if (
    hasRevenueMultiples
    && input.valuationContext.sampleSize >= 250
    && input.pricingStageCountrySectorSample >= 80
    && input.sourceConfidence !== "low"
    && input.valuationContext.sourceTier !== "C"
  ) {
    confidence = "high";
    reason = "Large revenue-multiple cohort with broad source coverage.";
  } else if (
    (hasRevenueMultiples && input.pricingRevenueSampleSize >= 60)
    || (!hasRevenueMultiples && input.pricingProxySampleSize >= 120)
  ) {
    confidence = "medium";
    reason = hasRevenueMultiples
      ? "Moderate revenue-multiple coverage; use with analyst review."
      : "Proxy-multiple context available, but revenue-linked comps are limited.";
  } else {
    confidence = "low";
    reason = "Valuation relies on sparse or proxy-only comparable pricing evidence.";
  }

  const penaltyReasons: string[] = [];
  let penalty = 0;
  const tierTotal = input.tierBreakdown.A + input.tierBreakdown.B + input.tierBreakdown.C;
  const tierARatio = tierTotal > 0 ? input.tierBreakdown.A / tierTotal : 0;
  const proxyRatio = (
    input.pricingRevenueSampleSize + input.pricingProxySampleSize > 0
      ? input.pricingProxySampleSize / (input.pricingRevenueSampleSize + input.pricingProxySampleSize)
      : 1
  );

  if (input.weightedCoverage < 0.60) {
    confidence = downgradeConfidence(confidence);
    penalty += 1;
    penaltyReasons.push("Low weighted source quality coverage.");
  }
  if (tierARatio < 0.30) {
    confidence = downgradeConfidence(confidence);
    penalty += 1;
    penaltyReasons.push("Tier-A evidence share is below 30%.");
  }
  if (!hasRevenueMultiples || proxyRatio > 0.55) {
    confidence = downgradeConfidence(confidence);
    penalty += 1;
    penaltyReasons.push("Valuation relies heavily on proxy multiples.");
  }
  if (input.pricingStageAlignedSample > 0 && input.pricingStageAlignedSample < 80) {
    confidence = downgradeConfidence(confidence);
    penalty += 1;
    penaltyReasons.push("Stage-aligned pricing sample is limited (<80 rows).");
  }
  if (
    input.pricingStageCountrySectorSample > 0
    && input.pricingStageCountrySectorSample < 80
  ) {
    confidence = downgradeConfidence(confidence);
    penalty += 2;
    penaltyReasons.push("Stage-country-sector matched pricing sample is limited (<80 rows).");
  }

  return {
    confidence,
    reason: penaltyReasons.length > 0
      ? `${reason} Penalties applied: ${penaltyReasons.join(" ")}`
      : reason,
    penalty,
    penaltyReasons,
  };
}

function chunk<T>(items: T[], size: number): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < items.length; i += size) {
    out.push(items.slice(i, i + size));
  }
  return out;
}

export async function queryCohort(
  supabase: AnySupabaseClient,
  filters: CohortFilters,
): Promise<OutcomeCohortRow[]> {
  const selectFields =
    "company_id, sector, country, stage_bucket, platform, campaign_date, funding_target, amount_raised, overfunding_ratio, pre_money_valuation, company_age_at_raise_months, had_revenue, revenue_at_raise, qualified_institutional_coinvestor, outcome, companies(name)";

  let query = supabase
    .from("crowdfunding_outcomes")
    .select(selectFields)
    .lte("label_quality_tier", 2)
    .in("outcome", ["failed", "trading", "exited"])
    .order("campaign_date", { ascending: false })
    .order("company_id", { ascending: true })
    .limit(900);

  if (filters.sector) query = query.eq("sector", filters.sector);
  if (filters.country) query = query.eq("country", filters.country);
  if (filters.stageBucket) query = query.eq("stage_bucket", filters.stageBucket);

  const { data, error } = await query;
  if (error || !data) return [];

  return data.map((r: Record<string, unknown>) => ({
    company_id: r.company_id as string,
    sector: r.sector as string | null,
    country: r.country as string | null,
    stage_bucket: r.stage_bucket as string | null,
    platform: r.platform as string | null,
    campaign_date: r.campaign_date as string | null,
    funding_target: r.funding_target as number | null,
    amount_raised: r.amount_raised as number | null,
    overfunding_ratio: r.overfunding_ratio as number | null,
    pre_money_valuation: r.pre_money_valuation as number | null,
    company_age_at_raise_months: r.company_age_at_raise_months as number | null,
    had_revenue: r.had_revenue as boolean | null,
    revenue_at_raise: r.revenue_at_raise as number | null,
    qualified_institutional_coinvestor: r.qualified_institutional_coinvestor as boolean | null,
    outcome: r.outcome as string,
    company_name: (r.companies as { name: string } | null)?.name ?? null,
  }));
}

async function queryPricingFromTrainingFeatures(
  supabase: AnySupabaseClient,
  filters: CohortFilters,
  limit = 1500,
): Promise<PricingCohortRow[]> {
  let query = supabase
    .from("training_features_wide")
    .select("entity_id, sector, country, stage_bucket, pre_money_valuation, valuation_cap, revenue_at_raise, amount_raised")
    .order("as_of_date", { ascending: false })
    .limit(limit);

  if (filters.sector) query = query.eq("sector", filters.sector);
  if (filters.country) query = query.eq("country", filters.country);
  if (filters.stageBucket) query = query.eq("stage_bucket", filters.stageBucket);

  const { data, error } = await query;
  if (error || !data) return [];

  const out: PricingCohortRow[] = [];
  for (const r of data as Record<string, unknown>[]) {
    const preMoney = r.pre_money_valuation as number | null;
    const valuationCap = r.valuation_cap as number | null;
    const effectivePreMoney = preMoney && preMoney > 0 ? preMoney : valuationCap;
    if (!effectivePreMoney || effectivePreMoney <= 0) continue;

    const capProxy = !preMoney && Boolean(valuationCap && valuationCap > 0);
    const sourceTier: "B" | "C" = capProxy ? "C" : "B";
    out.push({
      key: `tfw:${String(r.entity_id)}:${capProxy ? "cap" : "pre"}`,
      sector: r.sector as string | null,
      country: r.country as string | null,
      stageBucket: normalizeStageBucket(r.stage_bucket as string | null),
      pre_money_valuation: effectivePreMoney,
      revenue_at_raise: r.revenue_at_raise as number | null,
      amount_raised: r.amount_raised as number | null,
      source: capProxy ? "training_features:valuation_cap_proxy" : "training_features",
      sourceTier,
      sourceTierWeight: SOURCE_TIER_WEIGHT[sourceTier],
    });
  }
  return out;
}

async function queryPricingFromTransactionTruth(
  supabase: AnySupabaseClient,
  filters: CohortFilters,
  limit = 2000,
): Promise<PricingCohortRow[]> {
  let query = supabase
    .from("transaction_rounds")
    .select(
      "id, sector, country, stage_bucket, pre_money_valuation, valuation_cap, arr_revenue, amount_raised, source_tier, valuation_gate_pass, confidence_band",
    )
    .eq("valuation_gate_pass", true)
    .order("round_date", { ascending: false, nullsFirst: false })
    .limit(limit);

  if (filters.sector) query = query.eq("sector", filters.sector);
  if (filters.country) query = query.eq("country", filters.country);
  if (filters.stageBucket) query = query.eq("stage_bucket", filters.stageBucket);

  const { data, error } = await query;
  if (error || !data) return [];

  const out: PricingCohortRow[] = [];
  for (const row of data as Record<string, unknown>[]) {
    const confidenceBand = (row.confidence_band as string | null) ?? "low";
    if (confidenceBand === "low") continue;

    const preMoney = row.pre_money_valuation as number | null;
    const valuationCap = row.valuation_cap as number | null;
    const effectivePreMoney = preMoney && preMoney > 0 ? preMoney : valuationCap;
    if (!effectivePreMoney || effectivePreMoney <= 0) continue;

    const sourceTier = (row.source_tier as "A" | "B" | "C" | null) ?? "C";
    out.push({
      key: `tx:${String(row.id)}`,
      sector: row.sector as string | null,
      country: row.country as string | null,
      stageBucket: normalizeStageBucket(row.stage_bucket as string | null),
      pre_money_valuation: effectivePreMoney,
      revenue_at_raise: row.arr_revenue as number | null,
      amount_raised: row.amount_raised as number | null,
      source: "transaction_rounds_truth",
      sourceTier,
      sourceTierWeight: SOURCE_TIER_WEIGHT[sourceTier],
    });
  }

  return out;
}

async function queryPricingFromFundingRounds(
  supabase: AnySupabaseClient,
  filters: CohortFilters,
  limit = 1500,
): Promise<PricingCohortRow[]> {
  const { data, error } = await supabase
    .from("funding_rounds")
    .select("id, company_id, round_type, pre_money_valuation, valuation_cap, amount_raised, source, companies!inner(id, sector, country, source)")
    .order("round_date", { ascending: false })
    .limit(limit);

  if (error || !data) return [];

  const candidateRounds = (data as Record<string, unknown>[]).filter((row) => {
    const company = row.companies as { sector?: string | null; country?: string | null } | null;
    if (filters.sector && company?.sector !== filters.sector) return false;
    if (filters.country && company?.country !== filters.country) return false;
    if (filters.stageBucket) {
      const roundStage = normalizeStageBucket(row.round_type as string | null);
      if (roundStage !== filters.stageBucket) return false;
    }
    return true;
  });

  const companyIds = Array.from(
    new Set(
      candidateRounds
        .map((r) => r.company_id as string | null)
        .filter((v): v is string => Boolean(v)),
    ),
  );

  const revenueByCompany = new Map<string, number>();
  for (const ids of chunk(companyIds, 500)) {
    const { data: revenues } = await supabase
      .from("crowdfunding_outcomes")
      .select("company_id, campaign_date, revenue_at_raise")
      .lte("label_quality_tier", 2)
      .not("revenue_at_raise", "is", null)
      .gt("revenue_at_raise", 0)
      .in("company_id", ids)
      .order("campaign_date", { ascending: false });

    for (const rec of (revenues ?? []) as Array<Record<string, unknown>>) {
      const companyId = rec.company_id as string;
      if (!companyId || revenueByCompany.has(companyId)) continue;
      const revenue = rec.revenue_at_raise as number | null;
      if (revenue && revenue > 0) {
        revenueByCompany.set(companyId, revenue);
      }
    }

    // Fallback for non-crowdfunding sources: latest annual filing revenue.
    const { data: finRows } = await supabase
      .from("financial_data")
      .select("company_id, period_end_date, revenue")
      .eq("period_type", "annual")
      .not("revenue", "is", null)
      .gt("revenue", 0)
      .in("company_id", ids)
      .order("period_end_date", { ascending: false });

    for (const rec of (finRows ?? []) as Array<Record<string, unknown>>) {
      const companyId = rec.company_id as string;
      if (!companyId || revenueByCompany.has(companyId)) continue;
      const revenue = rec.revenue as number | null;
      if (revenue && revenue > 0) {
        revenueByCompany.set(companyId, revenue);
      }
    }
  }

  const out: PricingCohortRow[] = [];
  for (const r of candidateRounds) {
    const company = r.companies as { source?: string | null; sector?: string | null; country?: string | null } | null;
    const companySource = (company?.source ?? "unknown").toLowerCase();
    const sourceTier = tierForCompanySource(companySource);

    const companyId = r.company_id as string | null;
    const roundSource = (r.source as string | null) ?? companySource;

    const preMoney = r.pre_money_valuation as number | null;
    const valuationCap = r.valuation_cap as number | null;
    const effectivePreMoney = preMoney && preMoney > 0 ? preMoney : valuationCap;
    if (!effectivePreMoney || effectivePreMoney <= 0) continue;
    const capProxy = !preMoney && Boolean(valuationCap && valuationCap > 0);
    const effectiveTier = capProxy ? "C" : sourceTier;

    out.push({
      key: `round:${String(r.id)}`,
      sector: company?.sector ?? null,
      country: company?.country ?? null,
      stageBucket: normalizeStageBucket(r.round_type as string | null),
      pre_money_valuation: effectivePreMoney,
      revenue_at_raise: companyId ? (revenueByCompany.get(companyId) ?? null) : null,
      amount_raised: r.amount_raised as number | null,
      source: capProxy ? `funding_rounds:${roundSource}:valuation_cap_proxy` : `funding_rounds:${roundSource}`,
      sourceTier: effectiveTier,
      sourceTierWeight: SOURCE_TIER_WEIGHT[effectiveTier],
    });
  }
  return out;
}

async function queryPricingFromCrowdfundingOutcomes(
  supabase: AnySupabaseClient,
  filters: CohortFilters,
  limit = 1500,
): Promise<PricingCohortRow[]> {
  const { data, error } = await supabase
    .from("crowdfunding_outcomes")
    .select(
      "id, company_id, stage_bucket, pre_money_valuation, revenue_at_raise, amount_raised, companies!inner(source, sector, country)",
    )
    .lte("label_quality_tier", 2)
    .not("pre_money_valuation", "is", null)
    .gt("pre_money_valuation", 0)
    .order("campaign_date", { ascending: false })
    .limit(limit);

  if (error || !data) return [];

  const rows = data as Array<Record<string, unknown>>;
  return rows
    .filter((row) => {
      const company = row.companies as { sector?: string | null; country?: string | null } | null;
      if (filters.sector && company?.sector !== filters.sector) return false;
      if (filters.country && company?.country !== filters.country) return false;
      if (filters.stageBucket) {
        const stageBucket = normalizeStageBucket(row.stage_bucket as string | null);
        if (stageBucket !== filters.stageBucket) return false;
      }
      return true;
    })
    .map((row) => {
      const company = row.companies as { source?: string | null; sector?: string | null; country?: string | null } | null;
      const sourceTier = tierForCompanySource(company?.source);
      return {
        key: `co:${String(row.id)}`,
        sector: company?.sector ?? null,
        country: company?.country ?? null,
        stageBucket: normalizeStageBucket(row.stage_bucket as string | null),
        pre_money_valuation: row.pre_money_valuation as number | null,
        revenue_at_raise: row.revenue_at_raise as number | null,
        amount_raised: row.amount_raised as number | null,
        source: `crowdfunding_outcomes:${company?.source ?? "unknown"}`,
        sourceTier,
        sourceTierWeight: SOURCE_TIER_WEIGHT[sourceTier],
      };
    });
}

async function queryOfficialSignalCoverage(
  supabase: AnySupabaseClient,
  filters: CohortFilters,
): Promise<Array<{ signal_type: string | null }>> {
  let query = supabase
    .from("official_traction_signals")
    .select("signal_type, companies!inner(sector, country)")
    .order("signal_date", { ascending: false })
    .limit(2000);

  if (filters.sector) query = query.eq("companies.sector", filters.sector);
  if (filters.country) query = query.eq("companies.country", filters.country);

  const { data, error } = await query;
  if (error || !data) return [];
  return (data as Array<Record<string, unknown>>).map((row) => ({
    signal_type: row.signal_type as string | null,
  }));
}

export async function queryPricingCohort(
  supabase: AnySupabaseClient,
  filters: CohortFilters,
  opts?: {
    liteMode?: boolean;
    maxRows?: number;
  },
): Promise<PricingCohortRow[]> {
  const maxRows = opts?.maxRows ?? 1500;
  const [transactionRows, tfwRows, outcomeRows] = await Promise.all([
    queryPricingFromTransactionTruth(supabase, filters, maxRows),
    queryPricingFromTrainingFeatures(supabase, filters, maxRows),
    queryPricingFromCrowdfundingOutcomes(supabase, filters, maxRows),
  ]);
  if (opts?.liteMode) {
    const dedupLite = new Map<string, PricingCohortRow>();
    for (const row of [...transactionRows, ...outcomeRows, ...tfwRows]) {
      if (!dedupLite.has(row.key)) dedupLite.set(row.key, row);
    }
    return Array.from(dedupLite.values());
  }
  const roundRows = await queryPricingFromFundingRounds(supabase, filters, maxRows);

  const seen = new Set<string>();
  const out: PricingCohortRow[] = [];

  for (const row of [...transactionRows, ...outcomeRows, ...tfwRows, ...roundRows]) {
    if (seen.has(row.key)) continue;
    seen.add(row.key);
    out.push(row);
  }

  return out;
}

export async function findComparables(
  supabase: AnySupabaseClient,
  input: {
    sector?: string | null;
    country?: string | null;
    stageBucket?: string | null;
    fundingTarget?: number | null;
    preMoneyValuation?: number | null;
    companyAge?: number | null;
    revenue?: number | null;
    excludeCompanyId?: string | null;
  },
  options?: {
    minCohort?: number;
    queryCohortFn?: OutcomeQueryFn;
    queryPricingCohortFn?: PricingQueryFn;
    liteMode?: boolean;
    maxRows?: number;
    queryOfficialSignalsFn?: OfficialSignalQueryFn;
  },
): Promise<ComparablesResult | null> {
  const minCohort = options?.minCohort ?? 20;
  const queryCohortFn = options?.queryCohortFn ?? queryCohort;
  const queryPricingCohortFn = options?.queryPricingCohortFn
    ?? (options?.queryCohortFn
      ? async () => []
      : (sb: AnySupabaseClient, filters: CohortFilters) => queryPricingCohort(sb, filters, {
          liteMode: options?.liteMode,
          maxRows: options?.maxRows,
        }));
  const queryOfficialSignalsFn = options?.queryOfficialSignalsFn
    ?? (options?.queryCohortFn ? async () => [] : queryOfficialSignalCoverage);
  const stageLabel = input.stageBucket ? ` (${input.stageBucket})` : "";

  const attempts: Array<{ label: string; filters: CohortFilters }> = [];
  if (input.sector && input.country) {
    attempts.push({
      label: `${input.sector} companies in ${input.country}${stageLabel}`,
      filters: {
        sector: input.sector,
        country: input.country,
        stageBucket: input.stageBucket ?? null,
      },
    });
  }
  if (input.sector) {
    attempts.push({
      label: `${input.sector} companies${stageLabel}`,
      filters: { sector: input.sector, stageBucket: input.stageBucket ?? null },
    });
  }
  if (input.country) {
    attempts.push({
      label: `companies in ${input.country}${stageLabel}`,
      filters: { country: input.country, stageBucket: input.stageBucket ?? null },
    });
  }
  if (input.stageBucket) {
    attempts.push({
      label: `${input.stageBucket} companies`,
      filters: { stageBucket: input.stageBucket },
    });
  }
  attempts.push({
    label: input.stageBucket ? "all crowdfunding companies (all stages)" : "all crowdfunding companies",
    filters: {},
  });

  for (const attempt of attempts) {
    const [outcomeRowsRaw, pricingRows, officialSignals] = await Promise.all([
      queryCohortFn(supabase, attempt.filters),
      queryPricingCohortFn(supabase, attempt.filters),
      queryOfficialSignalsFn(supabase, attempt.filters),
    ]);
    const outcomeRows = input.excludeCompanyId
      ? outcomeRowsRaw.filter((r) => r.company_id !== input.excludeCompanyId)
      : outcomeRowsRaw;

    if (outcomeRows.length < minCohort && pricingRows.length < minCohort) {
      continue;
    }

    const cohortStats = computeStats(outcomeRows);

    const pricingSourceBreakdown: Record<string, number> = {};
    const pricingTierBreakdown: Record<"A" | "B" | "C", number> = { A: 0, B: 0, C: 0 };
    for (const row of pricingRows) {
      pricingSourceBreakdown[row.source] = (pricingSourceBreakdown[row.source] ?? 0) + 1;
      pricingTierBreakdown[row.sourceTier] += 1;
    }
    const weightedPricingCoverage = pricingRows.length > 0
      ? pricingRows.reduce((acc, row) => acc + row.sourceTierWeight, 0) / pricingRows.length
      : 0;
    const officialSignalTypeBreakdown: Record<string, number> = {};
    for (const signal of officialSignals) {
      const signalType = signal.signal_type ?? "unknown";
      officialSignalTypeBreakdown[signalType] = (officialSignalTypeBreakdown[signalType] ?? 0) + 1;
    }
    const officialSignalCount = officialSignals.length;

    const pricingRevenueMultiples = pricingRows
      .map((r) => {
        if (!r.pre_money_valuation || !r.revenue_at_raise || r.revenue_at_raise <= 0) {
          return null;
        }
        return r.pre_money_valuation / r.revenue_at_raise;
      })
      .filter((v): v is number => v !== null && isFinite(v) && v > 0);

    const pricingProxyMultiples = pricingRows
      .map((r) => {
        if (!r.pre_money_valuation || !r.amount_raised || r.amount_raised <= 0) {
          return null;
        }
        return r.pre_money_valuation / r.amount_raised;
      })
      .filter((v): v is number => v !== null && isFinite(v) && v > 0);

    const pricingStageAlignedSample = input.stageBucket
      ? pricingRows.filter((r) => r.stageBucket === input.stageBucket).length
      : pricingRows.length;
    const pricingStageCountrySectorSample = pricingRows.filter((r) => (
      (!input.stageBucket || r.stageBucket === input.stageBucket)
      && (!input.sector || r.sector === input.sector)
      && (!input.country || r.country === input.country)
    )).length;

    const outcomeMultiples = outcomeRows
      .map((r) => {
        if (!r.pre_money_valuation || !r.revenue_at_raise || r.revenue_at_raise <= 0) {
          return null;
        }
        return r.pre_money_valuation / r.revenue_at_raise;
      })
      .filter((v): v is number => v !== null && isFinite(v) && v > 0);

    const hasTierARevenue = pricingRows.some(
      (r) => r.sourceTier === "A" && r.revenue_at_raise !== null,
    );
    const valuationContext =
      buildValuationContext(pricingRevenueMultiples, input, {
        dataSource: "pricing_cohort",
        multipleType: "revenue_multiple",
        sourceTier: hasTierARevenue ? "A" : "B",
      })
      ?? buildValuationContext(pricingProxyMultiples, input, {
        dataSource: "pricing_cohort",
        multipleType: "raise_proxy_multiple",
        sourceTier: "C",
      })
      ?? buildValuationContext(outcomeMultiples, input, {
        dataSource: "outcome_cohort",
        multipleType: "revenue_multiple",
        sourceTier: "B",
      });

    const ranked = (options?.liteMode
      ? outcomeRows
        .filter((r) => r.company_name)
        .slice(0, 3)
        .map((r) => ({ row: r, dist: 0 }))
      : outcomeRows
        .filter((r) => r.company_name)
        .map((r) => ({
          row: r,
          dist: dealDistance(input, r),
        }))
        .sort((a, b) => a.dist - b.dist)
        .slice(0, 5));

    const nearestDeals: Comparable[] = ranked.map(({ row }) => ({
      name: row.company_name ?? "Unknown",
      sector: row.sector,
      country: row.country,
      stageBucket: row.stage_bucket,
      fundingTarget: row.funding_target,
      revenueAtRaise: row.revenue_at_raise,
      companyAgeMonths: row.company_age_at_raise_months,
      outcome: row.outcome,
      platform: row.platform,
      campaignDate: row.campaign_date,
    }));

    const outcomeSampleSize = outcomeRows.length;
    const pricingSampleSize = pricingRows.length;
    const sourceConfidence = inferConfidence(
      outcomeSampleSize,
      pricingSampleSize,
      pricingTierBreakdown,
    );
    const valuationConfidenceMeta = inferValuationConfidence({
      valuationContext,
      sourceConfidence,
      pricingRevenueSampleSize: pricingRevenueMultiples.length,
      pricingProxySampleSize: pricingProxyMultiples.length,
      pricingStageAlignedSample,
      pricingStageCountrySectorSample,
      tierBreakdown: pricingTierBreakdown,
      weightedCoverage: weightedPricingCoverage,
    });
    const pricingTierTotal = (
      pricingTierBreakdown.A + pricingTierBreakdown.B + pricingTierBreakdown.C
    );
    const pricingTierAShare = pricingTierTotal > 0
      ? pricingTierBreakdown.A / pricingTierTotal
      : 0;
    if (officialSignalCount < 20) {
      valuationConfidenceMeta.confidence = downgradeConfidence(valuationConfidenceMeta.confidence);
      valuationConfidenceMeta.penalty += 1;
      valuationConfidenceMeta.penaltyReasons.push(
        "Official traction signal coverage is sparse (<20 records).",
      );
    }

    return {
      cohortStats,
      cohortLabel: `${attempt.label} (outcome n=${outcomeSampleSize}, pricing n=${pricingSampleSize})`,
      nearestDeals,
      valuationContext,
      sourceSummary: {
        outcomeSampleSize,
        pricingSampleSize,
        pricingRevenueSampleSize: pricingRevenueMultiples.length,
        pricingProxySampleSize: pricingProxyMultiples.length,
        pricingStageAlignedSample,
        pricingStageCountrySectorSample,
        pricingSourceBreakdown,
        pricingTierBreakdown,
        pricingTierAShare,
        officialSignalCount,
        officialSignalTypeBreakdown,
        weightedPricingCoverage: Math.round(weightedPricingCoverage * 100) / 100,
        confidencePenalty: valuationConfidenceMeta.penalty,
        confidencePenaltyReasons: valuationConfidenceMeta.penaltyReasons,
      },
      sourceConfidence,
      valuationConfidence: valuationConfidenceMeta.confidence,
      valuationConfidenceReason: valuationConfidenceMeta.reason,
    };
  }

  return null;
}
