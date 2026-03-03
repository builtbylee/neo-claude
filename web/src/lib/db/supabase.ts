/**
 * Supabase client for server-side data access.
 *
 * Used by API routes to look up company features from the database.
 */

import { type SupabaseClient, createClient } from "@supabase/supabase-js";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnySupabaseClient = SupabaseClient<any, any, any>;

export function getSupabaseClient(
  url: string,
  key: string,
  accessToken?: string,
): AnySupabaseClient {
  return createClient(url, key, {
    global: accessToken
      ? {
          headers: { Authorization: `Bearer ${accessToken}` },
        }
      : undefined,
  });
}

export interface CompanyRow {
  id: string;
  name: string;
  sector: string | null;
  country: string | null;
  source: string;
  entity_id: string | null;
}

export interface FeatureRow {
  entity_id: string;
  stage_bucket: string | null;
  company_age_months: number | null;
  employee_count: number | null;
  revenue_at_raise: number | null;
  pre_revenue: boolean | null;
  total_assets: number | null;
  total_debt: number | null;
  debt_to_asset_ratio: number | null;
  cash_position: number | null;
  funding_target: number | null;
  amount_raised: number | null;
  overfunding_ratio: number | null;
  instrument_type: string | null;
  platform: string | null;
  country: string | null;
  sector: string | null;
}

/**
 * Look up a company by name (case-insensitive fuzzy match).
 */
export async function findCompany(
  supabase: AnySupabaseClient,
  name: string,
): Promise<CompanyRow | null> {
  // Try exact match first
  const { data: exact } = await supabase
    .from("companies")
    .select("id, name, sector, country, source, entity_id")
    .ilike("name", name)
    .limit(1);

  if (exact && exact.length > 0) return exact[0] as CompanyRow;

  // Try fuzzy match with LIKE
  const { data: fuzzy } = await supabase
    .from("companies")
    .select("id, name, sector, country, source, entity_id")
    .ilike("name", `%${name}%`)
    .limit(1);

  if (fuzzy && fuzzy.length > 0) return fuzzy[0] as CompanyRow;

  return null;
}

export interface DealTermsRow {
  instrument_type: string | null;
  round_type: string | null;
  amount_raised: number | null;
  pre_money_valuation: number | null;
  platform: string | null;
  round_date: string | null;
  overfunding_ratio: number | null;
  investor_count: number | null;
  funding_velocity_days: number | null;
  eis_seis_eligible: boolean | null;
  qsbs_eligible: boolean | null;
  qualified_institutional: boolean | null;
}

export interface FundingRoundRow extends DealTermsRow {
  id: string;
}

/**
 * Load deal terms from the most recent funding round for a company.
 */
export async function loadDealTerms(
  supabase: AnySupabaseClient,
  companyId: string,
): Promise<DealTermsRow | null> {
  const { data } = await supabase
    .from("funding_rounds")
    .select(
      "instrument_type, round_type, amount_raised, pre_money_valuation, platform, round_date, overfunding_ratio, investor_count, funding_velocity_days, eis_seis_eligible, qsbs_eligible, qualified_institutional",
    )
    .eq("company_id", companyId)
    .order("round_date", { ascending: false })
    .limit(1);

  if (data && data.length > 0) return data[0] as DealTermsRow;
  return null;
}

/**
 * Load historical funding rounds (most recent first).
 */
export async function loadFundingHistory(
  supabase: AnySupabaseClient,
  companyId: string,
  limit = 6,
): Promise<FundingRoundRow[]> {
  const { data } = await supabase
    .from("funding_rounds")
    .select(
      "id, instrument_type, round_type, amount_raised, pre_money_valuation, platform, round_date, overfunding_ratio, investor_count, funding_velocity_days, eis_seis_eligible, qsbs_eligible, qualified_institutional",
    )
    .eq("company_id", companyId)
    .order("round_date", { ascending: false })
    .limit(limit);

  if (!data) return [];
  return data as FundingRoundRow[];
}

export interface RegulatoryRow {
  current_status: string | null;
  source_id: string | null;
}

/**
 * Load Companies House regulatory data for a company.
 * Looks up by entity_id linkage to find the CH record.
 */
export async function loadRegulatoryData(
  supabase: AnySupabaseClient,
  entityId: string,
): Promise<RegulatoryRow | null> {
  // Find the Companies House record linked to this entity
  const { data } = await supabase
    .from("companies")
    .select("current_status, source_id")
    .eq("source", "companies_house")
    .eq("entity_id", entityId)
    .limit(1);

  if (data && data.length > 0) return data[0] as RegulatoryRow;
  return null;
}

/**
 * Load features for an entity from the training_features_wide matview.
 */
export async function loadFeatures(
  supabase: AnySupabaseClient,
  entityId: string,
): Promise<FeatureRow | null> {
  const { data } = await supabase
    .from("training_features_wide")
    .select("*")
    .eq("entity_id", entityId)
    .order("as_of_date", { ascending: false })
    .limit(1);

  if (data && data.length > 0) return data[0] as FeatureRow;
  return null;
}

export interface FeatureProvenanceRow {
  feature_name: string;
  source: string;
  as_of_date: string;
}

/**
 * Load latest provenance entries for selected features.
 */
export async function loadFeatureProvenance(
  supabase: AnySupabaseClient,
  entityId: string,
  featureNames: string[],
): Promise<FeatureProvenanceRow[]> {
  if (featureNames.length === 0) return [];

  const { data } = await supabase
    .from("feature_store")
    .select("feature_name, source, as_of_date")
    .eq("entity_id", entityId)
    .in("feature_name", featureNames)
    .order("as_of_date", { ascending: false });

  if (!data || data.length === 0) return [];

  // Keep only the latest entry per feature.
  const latestByFeature = new Map<string, FeatureProvenanceRow>();
  for (const row of data as FeatureProvenanceRow[]) {
    if (!latestByFeature.has(row.feature_name)) {
      latestByFeature.set(row.feature_name, row);
    }
  }

  return Array.from(latestByFeature.values());
}
