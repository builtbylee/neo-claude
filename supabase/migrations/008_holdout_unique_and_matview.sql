-- 008_holdout_unique_and_matview.sql
-- Add uniqueness constraint on backtest_holdout and regenerate materialized
-- view from feature registry (all 47 features instead of 3 examples).

-- Prevent duplicate quarantine entries for the same entity + window
CREATE UNIQUE INDEX IF NOT EXISTS idx_holdout_entity_window
  ON backtest_holdout(entity_id, holdout_window);

-- Drop the old example materialized view and recreate with all features
DROP MATERIALIZED VIEW IF EXISTS training_features_wide;

CREATE MATERIALIZED VIEW training_features_wide AS
SELECT
  entity_id,
  as_of_date,
  MAX(CASE WHEN feature_name = 'funding_target' THEN (feature_value->>'value')::numeric END) AS funding_target,
  MAX(CASE WHEN feature_name = 'amount_raised' THEN (feature_value->>'value')::numeric END) AS amount_raised,
  MAX(CASE WHEN feature_name = 'overfunding_ratio' THEN (feature_value->>'value')::numeric END) AS overfunding_ratio,
  MAX(CASE WHEN feature_name = 'equity_offered_pct' THEN (feature_value->>'value')::numeric END) AS equity_offered_pct,
  MAX(CASE WHEN feature_name = 'pre_money_valuation' THEN (feature_value->>'value')::numeric END) AS pre_money_valuation,
  MAX(CASE WHEN feature_name = 'investor_count' THEN (feature_value->>'value')::numeric END) AS investor_count,
  MAX(CASE WHEN feature_name = 'funding_velocity_days' THEN (feature_value->>'value')::numeric END) AS funding_velocity_days,
  BOOL_OR(CASE WHEN feature_name = 'eis_seis_eligible' THEN (feature_value->>'value')::boolean END) AS eis_seis_eligible,
  MAX(CASE WHEN feature_name = 'platform' THEN (feature_value->>'value')::text END) AS platform,
  MAX(CASE WHEN feature_name = 'company_age_months' THEN (feature_value->>'value')::numeric END) AS company_age_months,
  MAX(CASE WHEN feature_name = 'employee_count' THEN (feature_value->>'value')::numeric END) AS employee_count,
  MAX(CASE WHEN feature_name = 'revenue_at_raise' THEN (feature_value->>'value')::numeric END) AS revenue_at_raise,
  BOOL_OR(CASE WHEN feature_name = 'pre_revenue' THEN (feature_value->>'value')::boolean END) AS pre_revenue,
  MAX(CASE WHEN feature_name = 'revenue_growth_rate' THEN (feature_value->>'value')::numeric END) AS revenue_growth_rate,
  MAX(CASE WHEN feature_name = 'total_prior_funding' THEN (feature_value->>'value')::numeric END) AS total_prior_funding,
  BOOL_OR(CASE WHEN feature_name = 'prior_vc_backing' THEN (feature_value->>'value')::boolean END) AS prior_vc_backing,
  MAX(CASE WHEN feature_name = 'sector' THEN (feature_value->>'value')::text END) AS sector,
  MAX(CASE WHEN feature_name = 'revenue_model_type' THEN (feature_value->>'value')::text END) AS revenue_model_type,
  MAX(CASE WHEN feature_name = 'country' THEN (feature_value->>'value')::text END) AS country,
  MAX(CASE WHEN feature_name = 'founder_count' THEN (feature_value->>'value')::numeric END) AS founder_count,
  MAX(CASE WHEN feature_name = 'domain_experience_years' THEN (feature_value->>'value')::numeric END) AS domain_experience_years,
  BOOL_OR(CASE WHEN feature_name = 'prior_exits' THEN (feature_value->>'value')::boolean END) AS prior_exits,
  BOOL_OR(CASE WHEN feature_name = 'accelerator_alumni' THEN (feature_value->>'value')::boolean END) AS accelerator_alumni,
  MAX(CASE WHEN feature_name = 'total_assets' THEN (feature_value->>'value')::numeric END) AS total_assets,
  MAX(CASE WHEN feature_name = 'total_debt' THEN (feature_value->>'value')::numeric END) AS total_debt,
  MAX(CASE WHEN feature_name = 'debt_to_asset_ratio' THEN (feature_value->>'value')::numeric END) AS debt_to_asset_ratio,
  MAX(CASE WHEN feature_name = 'cash_position' THEN (feature_value->>'value')::numeric END) AS cash_position,
  MAX(CASE WHEN feature_name = 'burn_rate_monthly' THEN (feature_value->>'value')::numeric END) AS burn_rate_monthly,
  MAX(CASE WHEN feature_name = 'gross_margin' THEN (feature_value->>'value')::numeric END) AS gross_margin,
  MAX(CASE WHEN feature_name = 'instrument_type' THEN (feature_value->>'value')::text END) AS instrument_type,
  MAX(CASE WHEN feature_name = 'valuation_cap' THEN (feature_value->>'value')::numeric END) AS valuation_cap,
  MAX(CASE WHEN feature_name = 'discount_rate' THEN (feature_value->>'value')::numeric END) AS discount_rate,
  BOOL_OR(CASE WHEN feature_name = 'mfn_clause' THEN (feature_value->>'value')::boolean END) AS mfn_clause,
  MAX(CASE WHEN feature_name = 'liquidation_pref_multiple' THEN (feature_value->>'value')::numeric END) AS liquidation_pref_multiple,
  MAX(CASE WHEN feature_name = 'liquidation_participation' THEN (feature_value->>'value')::text END) AS liquidation_participation,
  MAX(CASE WHEN feature_name = 'seniority_position' THEN (feature_value->>'value')::numeric END) AS seniority_position,
  BOOL_OR(CASE WHEN feature_name = 'pro_rata_rights' THEN (feature_value->>'value')::boolean END) AS pro_rata_rights,
  BOOL_OR(CASE WHEN feature_name = 'qualified_institutional' THEN (feature_value->>'value')::boolean END) AS qualified_institutional,
  MAX(CASE WHEN feature_name = 'company_status' THEN (feature_value->>'value')::text END) AS company_status,
  BOOL_OR(CASE WHEN feature_name = 'accounts_overdue' THEN (feature_value->>'value')::boolean END) AS accounts_overdue,
  MAX(CASE WHEN feature_name = 'charges_count' THEN (feature_value->>'value')::numeric END) AS charges_count,
  MAX(CASE WHEN feature_name = 'director_disqualifications' THEN (feature_value->>'value')::numeric END) AS director_disqualifications,
  MAX(CASE WHEN feature_name = 'interest_rate_regime' THEN (feature_value->>'value')::text END) AS interest_rate_regime,
  MAX(CASE WHEN feature_name = 'equity_market_regime' THEN (feature_value->>'value')::text END) AS equity_market_regime,
  MAX(CASE WHEN feature_name = 'ecf_quarterly_volume' THEN (feature_value->>'value')::numeric END) AS ecf_quarterly_volume,
  MAX(CASE WHEN feature_name = 'data_source_count' THEN (feature_value->>'value')::numeric END) AS data_source_count,
  MAX(CASE WHEN feature_name = 'field_completeness_ratio' THEN (feature_value->>'value')::numeric END) AS field_completeness_ratio,
  MAX(label_quality_tier) AS worst_label_tier
FROM feature_store
WHERE label_quality_tier <= 2
GROUP BY entity_id, as_of_date;

CREATE UNIQUE INDEX IF NOT EXISTS idx_training_wide_entity
  ON training_features_wide(entity_id, as_of_date);
