/**
 * Comparables engine — finds similar crowdfunding deals and computes
 * cohort statistics for context.
 *
 * Queries crowdfunding_outcomes via Supabase to find deals in the same
 * sector/country/stage, then computes base rates and surfaces the
 * nearest neighbors.
 */

import { type SupabaseClient } from "@supabase/supabase-js";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnySupabaseClient = SupabaseClient<any, any, any>;

export interface CohortStats {
  sampleSize: number;
  failureRate: number;
  survivalRate: number;
  exitRate: number;
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

export interface ComparablesResult {
  cohortStats: CohortStats;
  cohortLabel: string;
  nearestDeals: Comparable[];
}

interface CohortRow {
  company_id: string;
  sector: string | null;
  country: string | null;
  stage_bucket: string | null;
  platform: string | null;
  campaign_date: string | null;
  funding_target: number | null;
  amount_raised: number | null;
  overfunding_ratio: number | null;
  company_age_at_raise_months: number | null;
  had_revenue: boolean | null;
  revenue_at_raise: number | null;
  qualified_institutional_coinvestor: boolean | null;
  outcome: string;
  company_name: string | null;
}

interface CohortFilters {
  sector?: string | null;
  country?: string | null;
  stageBucket?: string | null;
}

type CohortQueryFn = (
  supabase: AnySupabaseClient,
  filters: CohortFilters,
) => Promise<CohortRow[]>;

function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0
    ? (sorted[mid - 1] + sorted[mid]) / 2
    : sorted[mid];
}

function computeStats(rows: CohortRow[]): CohortStats {
  const total = rows.length;
  if (total === 0) {
    return {
      sampleSize: 0,
      failureRate: 0,
      survivalRate: 0,
      exitRate: 0,
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

  const institutional = rows.filter(
    (r) => r.qualified_institutional_coinvestor === true,
  ).length;
  const preRevenue = rows.filter(
    (r) => r.had_revenue === false || r.revenue_at_raise === null || r.revenue_at_raise === 0,
  ).length;

  return {
    sampleSize: total,
    failureRate: Math.round((failed / total) * 100) / 100,
    survivalRate: Math.round((trading / total) * 100) / 100,
    exitRate: Math.round((exited / total) * 100) / 100,
    medianFundingTarget: median(fundingTargets),
    medianRevenueAtRaise: median(revenues),
    medianCompanyAgeMonths: median(ages),
    medianOverfundingRatio: median(overfunding),
    pctWithInstitutional: Math.round((institutional / total) * 100) / 100,
    pctPreRevenue: Math.round((preRevenue / total) * 100) / 100,
  };
}

/**
 * Compute distance between a target deal and a cohort row for nearest-neighbor ranking.
 * Uses normalised absolute differences on available numeric fields.
 */
function dealDistance(
  target: { fundingTarget?: number | null; companyAge?: number | null; revenue?: number | null },
  row: CohortRow,
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

/**
 * Run a cohort query with the given filters.
 */
export async function queryCohort(
  supabase: AnySupabaseClient,
  filters: CohortFilters,
): Promise<CohortRow[]> {
  const selectFields =
    "company_id, sector, country, stage_bucket, platform, campaign_date, funding_target, amount_raised, overfunding_ratio, company_age_at_raise_months, had_revenue, revenue_at_raise, qualified_institutional_coinvestor, outcome, companies(name)";

  let query = supabase
    .from("crowdfunding_outcomes")
    .select(selectFields)
    .lte("label_quality_tier", 2)
    .in("outcome", ["failed", "trading", "exited"])
    .order("campaign_date", { ascending: false })
    .order("company_id", { ascending: true })
    .limit(1000);

  if (filters.sector) {
    query = query.eq("sector", filters.sector);
  }
  if (filters.country) {
    query = query.eq("country", filters.country);
  }
  if (filters.stageBucket) {
    query = query.eq("stage_bucket", filters.stageBucket);
  }

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
    company_age_at_raise_months: r.company_age_at_raise_months as number | null,
    had_revenue: r.had_revenue as boolean | null,
    revenue_at_raise: r.revenue_at_raise as number | null,
    qualified_institutional_coinvestor: r.qualified_institutional_coinvestor as boolean | null,
    outcome: r.outcome as string,
    company_name: (r.companies as { name: string } | null)?.name ?? null,
  }));
}

/**
 * Find comparable deals and compute cohort statistics.
 *
 * Progressively relaxes filters if the initial cohort is too small:
 * 1. sector + country
 * 2. sector only
 * 3. country only
 * 4. all deals
 */
export async function findComparables(
  supabase: AnySupabaseClient,
  input: {
    sector?: string | null;
    country?: string | null;
    stageBucket?: string | null;
    fundingTarget?: number | null;
    companyAge?: number | null;
    revenue?: number | null;
    excludeCompanyId?: string | null;
  },
  options?: {
    minCohort?: number;
    queryCohortFn?: CohortQueryFn;
  },
): Promise<ComparablesResult | null> {
  const minCohort = options?.minCohort ?? 20;
  const queryCohortFn = options?.queryCohortFn ?? queryCohort;

  const attempts: Array<{
    label: string;
    filters: CohortFilters;
  }> = [];
  const stageLabel = input.stageBucket ? ` (${input.stageBucket})` : "";

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
    const rows = await queryCohortFn(supabase, attempt.filters);
    const cohortRows = input.excludeCompanyId
      ? rows.filter((r) => r.company_id !== input.excludeCompanyId)
      : rows;

    if (cohortRows.length < minCohort) continue;

    const cohortStats = computeStats(cohortRows);

    // Find nearest neighbors
    const ranked = cohortRows
      .filter((r) => r.company_name)
      .map((r) => ({
        row: r,
        dist: dealDistance(input, r),
      }))
      .sort((a, b) => a.dist - b.dist)
      .slice(0, 5);

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

    return {
      cohortStats,
      cohortLabel: attempt.label,
      nearestDeals,
    };
  }

  return null;
}
