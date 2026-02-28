-- 003_training_data.sql
-- Create training data tables: companies, financial_data, funding_rounds,
-- stock_prices, ipo_outcomes, crowdfunding_outcomes.

CREATE TABLE IF NOT EXISTS companies (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  entity_id uuid REFERENCES canonical_entities,
  name text NOT NULL,
  ticker text,
  country text NOT NULL,
  sector text,
  sic_code text,
  founding_date date,
  ipo_date date,
  ipo_exchange text,
  source text NOT NULL,
  source_id text,
  current_status text,
  status_verified_date date,
  created_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS financial_data (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  company_id uuid REFERENCES companies,
  period_end_date date NOT NULL,
  period_type text,
  revenue numeric,
  revenue_growth_yoy numeric,
  gross_profit numeric,
  gross_margin numeric,
  operating_income numeric,
  net_income numeric,
  cash_and_equivalents numeric,
  total_assets numeric,
  total_liabilities numeric,
  total_debt numeric,
  employee_count integer,
  burn_rate_monthly numeric,
  customers integer,
  source_filing text
);

CREATE TABLE IF NOT EXISTS funding_rounds (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  company_id uuid REFERENCES companies,
  round_date date,
  round_type text,                  -- seed_equity, safe, convertible_note, series_a, etc.
  instrument_type text,             -- equity, safe, convertible_note, asa
  amount_raised numeric,
  pre_money_valuation numeric,
  post_money_valuation numeric,
  -- SAFE/convertible-specific terms
  valuation_cap numeric,            -- null for uncapped SAFEs (flag as high risk)
  discount_rate numeric,            -- e.g., 0.20 for 20% discount
  mfn_clause boolean,               -- most favoured nation
  interest_rate numeric,            -- convertible notes only
  maturity_date date,               -- convertible notes only
  -- Liquidation preference
  liquidation_preference_multiple numeric DEFAULT 1.0,  -- 1x, 2x, etc.
  liquidation_participation text,   -- non_participating, participating, capped_participating
  seniority_position integer,       -- 1 = most senior, higher = more junior
  -- Pro-rata rights
  pro_rata_rights boolean,
  pro_rata_amount numeric,
  -- Standard fields
  lead_investor text,
  qualified_institutional boolean,
  platform text,
  overfunding_ratio numeric,
  investor_count integer,
  funding_velocity_days integer,
  eis_seis_eligible boolean,
  qsbs_eligible boolean,            -- US Section 1202 qualification
  source text
);

CREATE TABLE IF NOT EXISTS stock_prices (
  company_id uuid REFERENCES companies,
  date date,
  close_price numeric,
  volume bigint,
  PRIMARY KEY (company_id, date)
);

CREATE TABLE IF NOT EXISTS ipo_outcomes (
  company_id uuid PRIMARY KEY REFERENCES companies,
  ipo_price numeric,
  ipo_market_cap numeric,
  macro_regime text,
  alpha_1yr numeric,
  alpha_3yr numeric,
  alpha_5yr numeric,
  return_1yr numeric,
  return_3yr numeric,
  return_5yr numeric,
  max_drawdown_3yr numeric,
  success_tier smallint
);

CREATE TABLE IF NOT EXISTS crowdfunding_outcomes (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  company_id uuid REFERENCES companies,
  platform text,
  campaign_date date,
  funding_target numeric,
  amount_raised numeric,
  overfunding_ratio numeric,
  equity_offered numeric,
  pre_money_valuation numeric,
  investor_count integer,
  funding_velocity_days integer,
  eis_seis_eligible boolean,
  qualified_institutional_coinvestor boolean,
  prior_vc_backing boolean,
  accelerator_alumni boolean,
  accelerator_name text,
  founder_count smallint,
  founder_domain_experience_years integer,
  founder_prior_exits boolean,
  had_revenue boolean,
  revenue_at_raise numeric,
  revenue_model text,
  company_age_at_raise_months integer,
  sector text,
  country text,
  stage_bucket text NOT NULL,       -- seed, early_growth
  outcome text NOT NULL,
  outcome_detail text,
  outcome_date date,
  years_to_outcome numeric,
  label_quality_tier smallint NOT NULL,  -- 1, 2, or 3
  data_source text
);
