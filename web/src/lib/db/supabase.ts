/**
 * Supabase client for server-side data access.
 *
 * Used by API routes to look up company features from the database.
 */

import { type SupabaseClient, createClient } from "@supabase/supabase-js";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnySupabaseClient = SupabaseClient<any, any, any>;

export function getSupabaseClient(url: string, key: string): AnySupabaseClient {
  return createClient(url, key);
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
