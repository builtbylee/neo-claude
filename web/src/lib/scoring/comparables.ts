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
    pricingSourceBreakdown: Record<string, number>;
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
  pre_money_valuation: number | null;
  revenue_at_raise: number | null;
  amount_raised: number | null;
  source: string;
  sourceTier: "A" | "B" | "C";
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
): "low" | "medium" | "high" {
  if (outcomeSampleSize >= 200 && pricingSampleSize >= 400) return "high";
  if (outcomeSampleSize >= 50 && pricingSampleSize >= 120) return "medium";
  return "low";
}

function inferValuationConfidence(input: {
  valuationContext: ValuationContext | null;
  sourceConfidence: "low" | "medium" | "high";
  pricingRevenueSampleSize: number;
  pricingProxySampleSize: number;
}): { confidence: "low" | "medium" | "high"; reason: string } {
  if (!input.valuationContext) {
    return {
      confidence: "low",
      reason: "Insufficient valuation comparables for a reliable pricing context.",
    };
  }

  const hasRevenueMultiples = input.valuationContext.multipleType === "revenue_multiple";
  if (
    hasRevenueMultiples
    && input.valuationContext.sampleSize >= 200
    && input.sourceConfidence !== "low"
    && input.valuationContext.sourceTier !== "C"
  ) {
    return {
      confidence: "high",
      reason: "Large revenue-multiple cohort with broad source coverage.",
    };
  }

  if (
    (hasRevenueMultiples && input.pricingRevenueSampleSize >= 60)
    || (!hasRevenueMultiples && input.pricingProxySampleSize >= 120)
  ) {
    return {
      confidence: "medium",
      reason: hasRevenueMultiples
        ? "Moderate revenue-multiple coverage; use with analyst review."
        : "Proxy-multiple context available, but revenue-linked comps are limited.",
    };
  }

  return {
    confidence: "low",
    reason: "Valuation relies on sparse or proxy-only comparable pricing evidence.",
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
    .select("entity_id, sector, country, pre_money_valuation, revenue_at_raise, amount_raised")
    .not("pre_money_valuation", "is", null)
    .gt("pre_money_valuation", 0)
    .order("as_of_date", { ascending: false })
    .limit(limit);

  if (filters.sector) query = query.eq("sector", filters.sector);
  if (filters.country) query = query.eq("country", filters.country);

  const { data, error } = await query;
  if (error || !data) return [];

  return (data as Record<string, unknown>[]).map((r) => ({
    key: `tfw:${String(r.entity_id)}`,
    sector: r.sector as string | null,
    country: r.country as string | null,
    pre_money_valuation: r.pre_money_valuation as number | null,
    revenue_at_raise: r.revenue_at_raise as number | null,
    amount_raised: r.amount_raised as number | null,
    source: "training_features",
    sourceTier: "B",
  }));
}

async function queryPricingFromFundingRounds(
  supabase: AnySupabaseClient,
  filters: CohortFilters,
  limit = 1500,
): Promise<PricingCohortRow[]> {
  const { data, error } = await supabase
    .from("funding_rounds")
    .select("id, company_id, pre_money_valuation, amount_raised, source, companies!inner(id, sector, country, source)")
    .not("pre_money_valuation", "is", null)
    .gt("pre_money_valuation", 0)
    .order("round_date", { ascending: false })
    .limit(limit);

  if (error || !data) return [];

  const candidateRounds = (data as Record<string, unknown>[]).filter((row) => {
    const company = row.companies as { sector?: string | null; country?: string | null } | null;
    if (filters.sector && company?.sector !== filters.sector) return false;
    if (filters.country && company?.country !== filters.country) return false;
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
  }

  return candidateRounds.map((r) => {
    const company = r.companies as { source?: string | null; sector?: string | null; country?: string | null } | null;
    const companySource = (company?.source ?? "unknown").toLowerCase();
    let sourceTier: "A" | "B" | "C" = "C";
    if (["sec_edgar", "sec_dera_cf", "companies_house"].includes(companySource)) {
      sourceTier = "A";
    } else if (["sec_form_d", "manual", "academic"].includes(companySource)) {
      sourceTier = "B";
    }

    const companyId = r.company_id as string | null;
    const roundSource = (r.source as string | null) ?? companySource;

    return {
      key: `round:${String(r.id)}`,
      sector: company?.sector ?? null,
      country: company?.country ?? null,
      pre_money_valuation: r.pre_money_valuation as number | null,
      revenue_at_raise: companyId ? (revenueByCompany.get(companyId) ?? null) : null,
      amount_raised: r.amount_raised as number | null,
      source: `funding_rounds:${roundSource}`,
      sourceTier,
    };
  });
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
  const tfwRows = await queryPricingFromTrainingFeatures(supabase, filters, maxRows);
  if (opts?.liteMode) {
    return tfwRows;
  }
  const roundRows = await queryPricingFromFundingRounds(supabase, filters, maxRows);

  const seen = new Set<string>();
  const out: PricingCohortRow[] = [];

  for (const row of [...tfwRows, ...roundRows]) {
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
    const [outcomeRowsRaw, pricingRows] = await Promise.all([
      queryCohortFn(supabase, attempt.filters),
      queryPricingCohortFn(supabase, attempt.filters),
    ]);
    const outcomeRows = input.excludeCompanyId
      ? outcomeRowsRaw.filter((r) => r.company_id !== input.excludeCompanyId)
      : outcomeRowsRaw;

    if (outcomeRows.length < minCohort && pricingRows.length < minCohort) {
      continue;
    }

    const cohortStats = computeStats(outcomeRows);

    const pricingSourceBreakdown: Record<string, number> = {};
    for (const row of pricingRows) {
      pricingSourceBreakdown[row.source] = (pricingSourceBreakdown[row.source] ?? 0) + 1;
    }

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
    const sourceConfidence = inferConfidence(outcomeSampleSize, pricingSampleSize);
    const valuationConfidenceMeta = inferValuationConfidence({
      valuationContext,
      sourceConfidence,
      pricingRevenueSampleSize: pricingRevenueMultiples.length,
      pricingProxySampleSize: pricingProxyMultiples.length,
    });

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
        pricingSourceBreakdown,
      },
      sourceConfidence,
      valuationConfidence: valuationConfidenceMeta.confidence,
      valuationConfidenceReason: valuationConfidenceMeta.reason,
    };
  }

  return null;
}
