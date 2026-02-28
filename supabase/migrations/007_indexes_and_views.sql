-- 007_indexes_and_views.sql
-- Create monitoring/governance tables, portfolio/funnel/sourcing tables,
-- all indexes, and the training_features_wide materialized view.

-- ============================================================
-- EVALUATIONS (must be created before tables that reference it)
-- ============================================================

CREATE TABLE IF NOT EXISTS evaluations (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  rubric_version_id integer REFERENCES rubric_versions,
  model_family text,                -- UK_Seed, US_EarlyGrowth, etc.
  model_version_id integer REFERENCES model_versions,
  evaluation_type text NOT NULL,    -- quick, deep, follow_on
  entity_id uuid REFERENCES canonical_entities,
  entity_match_confidence integer,
  company_name text NOT NULL,
  platform text,
  listing_url text,
  -- Input data
  manual_inputs jsonb NOT NULL,
  pitch_text text,
  founder_qa_text text,
  founder_content_text text,
  alt_data jsonb,
  alt_data_fetched_at timestamptz,
  -- Claude analysis (Stage 1)
  text_scores jsonb,
  -- Claude analysis (Stage 2 -- deep only)
  competitive_landscape text,
  qualitative_narrative text,
  qualitative_modifier integer,
  -- Claude analysis (Stage 3 -- deep only)
  pre_mortem jsonb,
  -- Return distribution (deep only)
  return_distribution jsonb,        -- {p10, p50, p90, expected_value,
                                    --  p10_with_eis, p50_with_eis, p90_with_eis,
                                    --  expected_value_with_eis}
  -- Valuation analysis (deep only)
  valuation_analysis jsonb,         -- {entry_multiple, sector_median,
                                    --  dilution_model, exit_path_assessment}
  -- Model outputs
  survival_probs jsonb,             -- {p_survive, p_exit, p_fail}
  progress_prob numeric,            -- 18-24 month milestone probability
  -- Scoring
  quantitative_score numeric NOT NULL,
  confidence_lower numeric NOT NULL,
  confidence_upper numeric NOT NULL,
  confidence_level text NOT NULL,
  category_scores jsonb NOT NULL,
  risk_flags jsonb,
  missing_data_fields jsonb,
  -- Decision engine
  abstention_gates jsonb,           -- {gate_name: {passed: bool, value: x, threshold: y}}
  kill_criteria_triggered jsonb,    -- [{criterion, source, action}] or null
  recommendation_class text NOT NULL,  -- invest, deep_diligence, watch, pass, abstain
  -- Quick Score recommendation (quick only)
  quick_recommendation text,
  -- Metadata
  created_at timestamptz DEFAULT now(),
  notes text
);

-- ============================================================
-- MONITORING & GOVERNANCE
-- ============================================================

CREATE TABLE IF NOT EXISTS calibration_log (
  id serial PRIMARY KEY,
  model_version_id integer REFERENCES model_versions,
  checked_at timestamptz DEFAULT now(),
  sample_size integer,
  ece numeric,
  brier_score numeric,
  status text,                      -- healthy, warning, critical
  notes text
);

CREATE TABLE IF NOT EXISTS prompt_calibration_log (
  id serial PRIMARY KEY,
  rubric_version_id integer REFERENCES rubric_versions,
  checked_at timestamptz DEFAULT now(),
  reference_set_mean numeric,       -- mean score on 20 reference pitches
  baseline_mean numeric,            -- original mean when prompt was deployed
  drift numeric,                    -- absolute difference
  status text,                      -- healthy, warning, critical
  notes text
);

CREATE TABLE IF NOT EXISTS recommendation_log (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  evaluation_id uuid REFERENCES evaluations,
  recommendation_class text NOT NULL,
  policy_override text,             -- null if no override; e.g., "annual_cap_exceeded"
  policy_override_reason text,
  user_override text,               -- null if no override; otherwise the action taken
  override_reason text,
  eventual_outcome text,            -- filled in later when outcome is known
  outcome_recorded_at timestamptz,
  created_at timestamptz DEFAULT now()
);

-- ============================================================
-- PORTFOLIO & ANTI-PORTFOLIO
-- ============================================================

CREATE TABLE IF NOT EXISTS investments (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  evaluation_id uuid REFERENCES evaluations,
  entity_id uuid REFERENCES canonical_entities,
  company_name text NOT NULL,
  platform text,
  invested_date date NOT NULL,
  amount_invested numeric NOT NULL,
  evaluation_score numeric,
  model_family text,
  rubric_version_id integer REFERENCES rubric_versions,
  -- Outcome tracking
  current_status text DEFAULT 'active',
  last_status_check date,
  outcome_date date,
  outcome_multiple numeric,
  follow_on_raises jsonb,
  outcome_notes text,
  created_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS anti_portfolio (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  evaluation_id uuid REFERENCES evaluations,
  entity_id uuid REFERENCES canonical_entities,
  company_name text NOT NULL,
  platform text,
  passed_date date NOT NULL,
  evaluation_score numeric,
  evaluation_type text,
  pass_reason text NOT NULL,
  pass_notes text,
  rubric_version_id integer REFERENCES rubric_versions,
  -- Outcome tracking
  current_status text,
  last_status_check date,
  subsequent_raise_amount numeric,
  subsequent_raise_valuation numeric,
  outcome_notes text,
  created_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS follow_on_evaluations (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  original_investment_id uuid REFERENCES investments,
  evaluation_id uuid REFERENCES evaluations,
  follow_on_round_date date,
  new_valuation numeric,
  new_round_amount numeric,
  milestones_hit jsonb,             -- [{milestone, met: boolean}, ...]
  progress_model_prediction numeric,  -- what did the model predict at investment?
  actual_progress boolean,          -- did the milestone actually happen?
  recommendation text,              -- increase, maintain, do_not_follow
  recommendation_reasoning text,
  created_at timestamptz DEFAULT now()
);

-- ============================================================
-- SELECTION-BIAS FUNNEL
-- ============================================================

CREATE TABLE IF NOT EXISTS deal_funnel (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  entity_id uuid REFERENCES canonical_entities,
  company_name text NOT NULL,
  platform text,
  sector text,
  country text,
  -- Funnel stages (timestamped)
  existed_at timestamptz,           -- when the deal appeared (sourcing alert or manual log)
  seen_at timestamptz,              -- when you opened/viewed the listing
  assessed_at timestamptz,          -- when you ran Quick or Deep Score
  evaluation_id uuid REFERENCES evaluations,
  decision text,                    -- invested, passed, watching, not_assessed
  decision_at timestamptz,
  -- Outcome (for non-assessed deals -- reveals what you missed)
  outcome text,                     -- unknown, trading, failed, raised_again, exited
  outcome_checked_at timestamptz,
  notes text,
  created_at timestamptz DEFAULT now()
);

-- ============================================================
-- DEAL SOURCING
-- ============================================================

CREATE TABLE IF NOT EXISTS deal_alerts (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  source text NOT NULL,
  company_name text NOT NULL,
  sector text,
  offering_amount numeric,
  revenue numeric,
  listing_url text,
  alert_date timestamptz DEFAULT now(),
  status text DEFAULT 'new',
  evaluation_id uuid REFERENCES evaluations,
  deal_funnel_id uuid REFERENCES deal_funnel,  -- auto-linked
  notes text
);

CREATE TABLE IF NOT EXISTS alert_criteria (
  id serial PRIMARY KEY,
  sectors jsonb NOT NULL,
  min_revenue numeric,
  max_offering_amount numeric,
  countries jsonb,
  eis_required boolean DEFAULT false,
  active boolean DEFAULT true
);

-- ============================================================
-- INDEXES
-- ============================================================

-- Feature store: training queries pivot on entity + date
CREATE INDEX IF NOT EXISTS idx_feature_store_entity_date ON feature_store(entity_id, as_of_date);
CREATE INDEX IF NOT EXISTS idx_feature_store_family ON feature_store(feature_family, feature_name);

-- Evaluations: portfolio views, monitoring queries
CREATE INDEX IF NOT EXISTS idx_evaluations_entity ON evaluations(entity_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_evaluations_model ON evaluations(model_family, recommendation_class);
CREATE INDEX IF NOT EXISTS idx_evaluations_type ON evaluations(evaluation_type, created_at DESC);

-- Entity resolution: lookup by source identifier
CREATE INDEX IF NOT EXISTS idx_entity_links_source ON entity_links(source, source_identifier);
CREATE INDEX IF NOT EXISTS idx_entity_links_entity ON entity_links(entity_id);

-- Deal funnel: sourcing analysis
CREATE INDEX IF NOT EXISTS idx_deal_funnel_platform ON deal_funnel(platform, existed_at DESC);
CREATE INDEX IF NOT EXISTS idx_deal_funnel_sector ON deal_funnel(sector, country);

-- Portfolio: outcome tracking
CREATE INDEX IF NOT EXISTS idx_investments_status ON investments(current_status, invested_date DESC);
CREATE INDEX IF NOT EXISTS idx_anti_portfolio_date ON anti_portfolio(passed_date DESC);

-- Monitoring: calibration and recommendation audit
CREATE INDEX IF NOT EXISTS idx_calibration_log_model ON calibration_log(model_version_id, checked_at DESC);
CREATE INDEX IF NOT EXISTS idx_recommendation_log_eval ON recommendation_log(evaluation_id);
CREATE INDEX IF NOT EXISTS idx_recommendation_log_override ON recommendation_log(user_override) WHERE user_override IS NOT NULL;

-- Training data: model training queries
CREATE INDEX IF NOT EXISTS idx_crowdfunding_outcomes_stage ON crowdfunding_outcomes(stage_bucket, country, label_quality_tier);
CREATE INDEX IF NOT EXISTS idx_companies_entity ON companies(entity_id);

-- Backtest: holdout lookup
CREATE INDEX IF NOT EXISTS idx_backtest_holdout_window ON backtest_holdout(holdout_window);

-- ============================================================
-- MATERIALIZED VIEW: training_features_wide
-- ============================================================
-- Pivots the EAV feature_store into a wide feature matrix for XGBoost.
-- Refreshed before each training run.
--
-- NOTE: The pivot columns below are examples. The full list of feature
-- columns should be generated programmatically from the feature registry
-- (one MAX(CASE ...) per registered feature_name).

CREATE MATERIALIZED VIEW IF NOT EXISTS training_features_wide AS
SELECT
  entity_id,
  as_of_date,
  MAX(CASE WHEN feature_name = 'revenue_at_raise' THEN (feature_value->>'value')::numeric END) AS revenue_at_raise,
  MAX(CASE WHEN feature_name = 'employee_count' THEN (feature_value->>'value')::numeric END) AS employee_count,
  MAX(CASE WHEN feature_name = 'company_age_months' THEN (feature_value->>'value')::numeric END) AS company_age_months,
  -- ... (one column per feature, generated programmatically from feature registry)
  MAX(label_quality_tier) AS worst_label_tier
FROM feature_store
WHERE label_quality_tier <= 2  -- exclude Tier 3
GROUP BY entity_id, as_of_date;

CREATE UNIQUE INDEX IF NOT EXISTS idx_training_wide_entity ON training_features_wide(entity_id, as_of_date);
