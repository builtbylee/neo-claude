-- 015_free_data_quality_and_compliance.sql
-- Free-data quality layer for valuation confidence, segment evidence, and sanctions checks.

CREATE TABLE IF NOT EXISTS segment_model_evidence (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  segment_key text NOT NULL UNIQUE,                     -- US_Seed, US_EarlyGrowth, UK_Seed, UK_EarlyGrowth
  sample_size integer NOT NULL DEFAULT 0,
  survival_auc numeric,
  calibration_ece numeric,
  release_gate_open boolean NOT NULL DEFAULT false,
  last_backtest_run_id integer REFERENCES backtest_runs,
  last_backtest_date date,
  source_coverage jsonb NOT NULL DEFAULT '{}'::jsonb,
  notes text,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now(),
  CONSTRAINT chk_segment_model_evidence_segment
    CHECK (segment_key IN ('US_Seed', 'US_EarlyGrowth', 'UK_Seed', 'UK_EarlyGrowth'))
);

CREATE TABLE IF NOT EXISTS valuation_scenario_audits (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  company_id uuid REFERENCES companies,
  entity_id uuid REFERENCES canonical_entities,
  evaluation_type text NOT NULL DEFAULT 'quick',       -- quick, deep, follow_on
  segment_key text,
  recommendation_class text,
  score numeric,
  data_completeness numeric,
  valuation_confidence text,                           -- high, medium, low
  valuation_confidence_reason text,
  valuation_source_summary jsonb,
  entry_multiple numeric,
  bear_moic numeric,
  base_moic numeric,
  bull_moic numeric,
  realized_status text,                                -- unknown, failed, trading, exited
  realized_moic numeric,
  realized_at date,
  calibration_error numeric,
  notes text,
  created_at timestamptz DEFAULT now(),
  CONSTRAINT chk_valuation_audits_confidence
    CHECK (
      valuation_confidence IS NULL
      OR valuation_confidence IN ('high', 'medium', 'low')
    ),
  CONSTRAINT chk_valuation_audits_segment
    CHECK (
      segment_key IS NULL
      OR segment_key IN ('US_Seed', 'US_EarlyGrowth', 'UK_Seed', 'UK_EarlyGrowth')
    )
);

CREATE TABLE IF NOT EXISTS sanctions_screenings (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  screened_name text NOT NULL,
  normalized_name text NOT NULL,
  matched boolean NOT NULL DEFAULT false,
  match_source text,                                   -- ofac_sdn, uk_sanctions
  match_name text,
  risk_level text NOT NULL DEFAULT 'clear',           -- clear, potential_match
  details jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz DEFAULT now(),
  CONSTRAINT chk_sanctions_screenings_risk
    CHECK (risk_level IN ('clear', 'potential_match'))
);

CREATE INDEX IF NOT EXISTS idx_segment_model_evidence_backtest
  ON segment_model_evidence(last_backtest_date DESC, segment_key);

CREATE INDEX IF NOT EXISTS idx_valuation_scenario_audits_segment_created
  ON valuation_scenario_audits(segment_key, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_valuation_scenario_audits_realized
  ON valuation_scenario_audits(realized_status, realized_at DESC);

CREATE INDEX IF NOT EXISTS idx_sanctions_screenings_created
  ON sanctions_screenings(created_at DESC, matched);

INSERT INTO segment_model_evidence (segment_key)
SELECT v.segment_key
FROM (
  VALUES
    ('US_Seed'),
    ('US_EarlyGrowth'),
    ('UK_Seed'),
    ('UK_EarlyGrowth')
) AS v(segment_key)
WHERE NOT EXISTS (
  SELECT 1
  FROM segment_model_evidence s
  WHERE s.segment_key = v.segment_key
);

