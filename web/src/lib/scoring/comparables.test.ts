import assert from "node:assert/strict";
import test from "node:test";

import { findComparables, queryCohort } from "./comparables";

type CohortRow = {
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
};

function row(
  id: number,
  overrides: Partial<CohortRow> = {},
): CohortRow {
  return {
    company_id: `company-${id}`,
    sector: "Fintech",
    country: "UK",
    stage_bucket: "seed",
    platform: "crowdcube",
    campaign_date: "2024-01-01",
    funding_target: 500_000 + id,
    amount_raised: 550_000 + id,
    overfunding_ratio: 1.1,
    pre_money_valuation: 2_000_000 + id * 10_000,
    company_age_at_raise_months: 24,
    had_revenue: true,
    revenue_at_raise: 250_000 + id,
    qualified_institutional_coinvestor: false,
    outcome: id % 3 === 0 ? "failed" : "trading",
    company_name: `Company ${id}`,
    ...overrides,
  };
}

test("findComparables uses staged fallback and preserves stage filter", async () => {
  const calls: Array<{ sector?: string | null; country?: string | null; stageBucket?: string | null }> = [];

  const result = await findComparables(
    {} as never,
    {
      sector: "Fintech",
      country: "UK",
      stageBucket: "seed",
      fundingTarget: 500_000,
      companyAge: 24,
      revenue: 200_000,
    },
    {
      minCohort: 20,
      queryCohortFn: async (_supabase, filters) => {
        calls.push(filters);
        const isSectorCountryStage = filters.sector === "Fintech" && filters.country === "UK" && filters.stageBucket === "seed";
        if (isSectorCountryStage) {
          return Array.from({ length: 8 }, (_, i) => row(i + 1));
        }
        const isSectorStage = filters.sector === "Fintech" && !filters.country && filters.stageBucket === "seed";
        if (isSectorStage) {
          return Array.from({ length: 24 }, (_, i) => row(i + 1));
        }
        return [];
      },
    },
  );

  assert.ok(result);
  assert.equal(result.cohortStats.sampleSize, 24);
  assert.equal(result.cohortStats.medianRevenueMultiple !== null, true);
  assert.match(result.cohortLabel, /Fintech companies \(seed\)/);
  assert.equal(calls.length, 2);
  assert.deepEqual(calls[0], { sector: "Fintech", country: "UK", stageBucket: "seed" });
  assert.deepEqual(calls[1], { sector: "Fintech", stageBucket: "seed" });
});

test("findComparables excludes matched company from cohort stats and nearest deals", async () => {
  const rows = Array.from({ length: 21 }, (_, i) =>
    row(i + 1, i === 0 ? { company_id: "target-company", company_name: "Target Co" } : {}),
  );

  const result = await findComparables(
    {} as never,
    {
      sector: "Fintech",
      country: "UK",
      stageBucket: "seed",
      excludeCompanyId: "target-company",
      fundingTarget: 500_000,
      companyAge: 24,
      revenue: 200_000,
    },
    {
      minCohort: 20,
      queryCohortFn: async () => rows,
    },
  );

  assert.ok(result);
  assert.equal(result.cohortStats.sampleSize, 20);
  assert.equal(
    result.nearestDeals.some((d) => d.name === "Target Co"),
    false,
  );
});

test("findComparables computes valuation context when valuation and revenue are present", async () => {
  const rows = Array.from({ length: 30 }, (_, i) =>
    row(i + 1, {
      pre_money_valuation: 2_000_000 + i * 200_000,
      revenue_at_raise: 200_000,
    }),
  );

  const result = await findComparables(
    {} as never,
    {
      sector: "Fintech",
      country: "UK",
      stageBucket: "seed",
      preMoneyValuation: 6_000_000,
      revenue: 300_000,
    },
    {
      minCohort: 20,
      queryCohortFn: async () => rows,
    },
  );

  assert.ok(result);
  assert.ok(result.valuationContext);
  assert.equal(result.valuationContext?.impliedRevenueMultiple, 20);
});

test("queryCohort applies deterministic ordering and all filters", async () => {
  const eqCalls: Array<[string, unknown]> = [];
  const orderCalls: Array<[string, { ascending: boolean }]> = [];

  const query = {
    select: () => query,
    lte: () => query,
    in: () => query,
    order: (column: string, opts: { ascending: boolean }) => {
      orderCalls.push([column, opts]);
      return query;
    },
    limit: () => query,
    eq: (column: string, value: unknown) => {
      eqCalls.push([column, value]);
      return query;
    },
    then: (resolve: (value: { data: []; error: null }) => void) => {
      resolve({ data: [], error: null });
    },
  };

  const supabase = {
    from: () => query,
  };

  const result = await queryCohort(supabase as never, {
    sector: "Fintech",
    country: "UK",
    stageBucket: "seed",
  });

  assert.deepEqual(result, []);
  assert.deepEqual(eqCalls, [
    ["sector", "Fintech"],
    ["country", "UK"],
    ["stage_bucket", "seed"],
  ]);
  assert.deepEqual(orderCalls, [
    ["campaign_date", { ascending: false }],
    ["company_id", { ascending: true }],
  ]);
});

test("findComparables applies source-tier confidence penalties", async () => {
  const outcomeRows = Array.from({ length: 80 }, (_, i) => row(i + 1));
  const pricingRows = Array.from({ length: 140 }, (_, i) => ({
    key: `pricing-${i}`,
    sector: "Fintech",
    country: "UK",
    stageBucket: "seed",
    pre_money_valuation: 6_000_000 + i * 10_000,
    revenue_at_raise: null,
    amount_raised: 200_000 + i * 100,
    source: "proxy_only",
    sourceTier: "C" as const,
    sourceTierWeight: 0.4,
  }));

  const result = await findComparables(
    {} as never,
    {
      sector: "Fintech",
      country: "UK",
      stageBucket: "seed",
      preMoneyValuation: 8_000_000,
      fundingTarget: 300_000,
    },
    {
      minCohort: 20,
      queryCohortFn: async () => outcomeRows,
      queryPricingCohortFn: async () => pricingRows,
    },
  );

  assert.ok(result);
  assert.equal(result.valuationContext?.multipleType, "raise_proxy_multiple");
  assert.equal(result.sourceSummary.pricingTierBreakdown.C, 140);
  assert.equal(result.sourceSummary.pricingStageAlignedSample, 140);
  assert.equal(result.sourceSummary.pricingStageCountrySectorSample, 140);
  assert.equal(result.sourceSummary.pricingTierAShare, 0);
  assert.equal(result.valuationConfidence, "low");
  assert.ok(result.sourceSummary.confidencePenalty > 0);
  assert.ok(result.sourceSummary.confidencePenaltyReasons.length > 0);
});
