-- 024_transaction_truth_and_free_source_spines.sql
-- Canonical transaction truth model, reconciliation provenance, and analyst-grade valuation gate fields.

CREATE TABLE IF NOT EXISTS transaction_rounds (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  company_id uuid REFERENCES companies,
  entity_id uuid REFERENCES canonical_entities,
  country text,
  sector text,
  stage_bucket text,
  round_stitch_key text NOT NULL UNIQUE,
  round_type text,
  instrument_type text,
  round_date date,
  amount_raised numeric,
  pre_money_valuation numeric,
  post_money_valuation numeric,
  valuation_cap numeric,
  discount_rate numeric,
  interest_rate numeric,
  maturity_date date,
  liquidation_preference_multiple numeric,
  liquidation_participation text,
  pro_rata_rights boolean,
  lead_investor text,
  lead_investor_quality numeric,
  arr_revenue numeric,
  revenue_growth_yoy numeric,
  burn_rate_monthly numeric,
  runway_months numeric,
  source_timestamp timestamptz,
  source_tier text NOT NULL DEFAULT 'C',
  source_count integer NOT NULL DEFAULT 0,
  conflict_count integer NOT NULL DEFAULT 0,
  core_term_completeness numeric NOT NULL DEFAULT 0,
  confidence_score numeric NOT NULL DEFAULT 0,
  confidence_band text NOT NULL DEFAULT 'low',
  valuation_gate_pass boolean NOT NULL DEFAULT false,
  valuation_gate_reason text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT chk_transaction_rounds_tier
    CHECK (source_tier IN ('A', 'B', 'C')),
  CONSTRAINT chk_transaction_rounds_confidence_band
    CHECK (confidence_band IN ('low', 'medium', 'high')),
  CONSTRAINT chk_transaction_rounds_core_term_completeness
    CHECK (core_term_completeness >= 0 AND core_term_completeness <= 1),
  CONSTRAINT chk_transaction_rounds_confidence_score
    CHECK (confidence_score >= 0 AND confidence_score <= 1),
  CONSTRAINT chk_transaction_rounds_lead_quality
    CHECK (lead_investor_quality IS NULL OR (lead_investor_quality >= 0 AND lead_investor_quality <= 1))
);

CREATE INDEX IF NOT EXISTS idx_transaction_rounds_company_date
  ON transaction_rounds(company_id, round_date DESC);
CREATE INDEX IF NOT EXISTS idx_transaction_rounds_segment
  ON transaction_rounds(country, stage_bucket, sector, round_date DESC);
CREATE INDEX IF NOT EXISTS idx_transaction_rounds_confidence
  ON transaction_rounds(confidence_band, valuation_gate_pass, round_date DESC);

CREATE TABLE IF NOT EXISTS transaction_round_source_records (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  transaction_round_id uuid NOT NULL REFERENCES transaction_rounds ON DELETE CASCADE,
  source_name text NOT NULL,
  source_record_id text,
  source_url text,
  source_timestamp timestamptz,
  source_tier text NOT NULL DEFAULT 'C',
  raw_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (transaction_round_id, source_name, source_record_id),
  CONSTRAINT chk_transaction_round_source_tier
    CHECK (source_tier IN ('A', 'B', 'C'))
);

CREATE INDEX IF NOT EXISTS idx_transaction_round_source_round
  ON transaction_round_source_records(transaction_round_id, source_name);

CREATE TABLE IF NOT EXISTS transaction_round_field_facts (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  transaction_round_id uuid NOT NULL REFERENCES transaction_rounds ON DELETE CASCADE,
  field_name text NOT NULL,
  field_value jsonb NOT NULL,
  source_name text NOT NULL,
  source_record_id text,
  source_tier text NOT NULL DEFAULT 'C',
  as_of_timestamp timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT chk_transaction_round_fact_tier
    CHECK (source_tier IN ('A', 'B', 'C'))
);

CREATE INDEX IF NOT EXISTS idx_transaction_round_facts_round_field
  ON transaction_round_field_facts(transaction_round_id, field_name, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_transaction_round_facts_source
  ON transaction_round_field_facts(source_name, source_record_id);

CREATE TABLE IF NOT EXISTS transaction_round_field_truth (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  transaction_round_id uuid NOT NULL REFERENCES transaction_rounds ON DELETE CASCADE,
  field_name text NOT NULL,
  reconciled_value jsonb NOT NULL,
  source_names jsonb NOT NULL DEFAULT '[]'::jsonb,
  source_record_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
  as_of_timestamp timestamptz,
  conflict_state text NOT NULL DEFAULT 'none',
  confidence numeric NOT NULL DEFAULT 0,
  evidence_count integer NOT NULL DEFAULT 0,
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (transaction_round_id, field_name),
  CONSTRAINT chk_transaction_round_truth_conflict
    CHECK (conflict_state IN ('none', 'minor', 'major')),
  CONSTRAINT chk_transaction_round_truth_confidence
    CHECK (confidence >= 0 AND confidence <= 1)
);

CREATE INDEX IF NOT EXISTS idx_transaction_round_truth_round
  ON transaction_round_field_truth(transaction_round_id, field_name);
CREATE INDEX IF NOT EXISTS idx_transaction_round_truth_conflict
  ON transaction_round_field_truth(conflict_state, updated_at DESC);

CREATE TABLE IF NOT EXISTS transaction_round_qa_audits (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  audit_week date NOT NULL,
  transaction_round_id uuid NOT NULL REFERENCES transaction_rounds ON DELETE CASCADE,
  field_name text NOT NULL,
  truth_value jsonb,
  source_value jsonb,
  is_match boolean,
  reviewer text,
  reviewed_at timestamptz,
  notes text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_transaction_round_qa_week
  ON transaction_round_qa_audits(audit_week DESC, field_name);
CREATE INDEX IF NOT EXISTS idx_transaction_round_qa_round
  ON transaction_round_qa_audits(transaction_round_id, created_at DESC);

CREATE TABLE IF NOT EXISTS investor_references (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  sec_identifier text,
  crd_number text,
  legal_name text NOT NULL,
  normalized_name text NOT NULL UNIQUE,
  adviser_status text NOT NULL DEFAULT 'unknown',
  regulatory_assets_usd numeric,
  disciplinary_events integer NOT NULL DEFAULT 0,
  quality_tier text NOT NULL DEFAULT 'C',
  source_name text NOT NULL,
  source_timestamp timestamptz,
  raw_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT chk_investor_references_status
    CHECK (adviser_status IN ('registered', 'exempt', 'unknown')),
  CONSTRAINT chk_investor_references_quality_tier
    CHECK (quality_tier IN ('A', 'B', 'C'))
);

CREATE INDEX IF NOT EXISTS idx_investor_references_name
  ON investor_references(normalized_name);
CREATE INDEX IF NOT EXISTS idx_investor_references_quality
  ON investor_references(quality_tier, regulatory_assets_usd DESC NULLS LAST);

CREATE TABLE IF NOT EXISTS transaction_round_investors (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  transaction_round_id uuid NOT NULL REFERENCES transaction_rounds ON DELETE CASCADE,
  investor_name text NOT NULL,
  normalized_name text NOT NULL,
  investor_role text NOT NULL DEFAULT 'lead',
  investor_reference_id uuid REFERENCES investor_references,
  quality_score numeric,
  source_name text NOT NULL,
  source_record_id text,
  as_of_timestamp timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (transaction_round_id, normalized_name, investor_role),
  CONSTRAINT chk_transaction_round_investor_quality
    CHECK (quality_score IS NULL OR (quality_score >= 0 AND quality_score <= 1))
);

CREATE INDEX IF NOT EXISTS idx_transaction_round_investors_round
  ON transaction_round_investors(transaction_round_id, investor_role);
CREATE INDEX IF NOT EXISTS idx_transaction_round_investors_ref
  ON transaction_round_investors(investor_reference_id);

CREATE TABLE IF NOT EXISTS official_traction_signals (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  company_id uuid REFERENCES companies,
  entity_id uuid REFERENCES canonical_entities,
  signal_type text NOT NULL,
  signal_date date NOT NULL,
  signal_value numeric,
  confidence numeric NOT NULL DEFAULT 0.5,
  source_name text NOT NULL,
  source_tier text NOT NULL DEFAULT 'B',
  source_url text,
  details jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT chk_official_signal_type
    CHECK (signal_type IN ('uk_public_contracts', 'ukri_grants', 'uspto_patents')),
  CONSTRAINT chk_official_signal_confidence
    CHECK (confidence >= 0 AND confidence <= 1),
  CONSTRAINT chk_official_signal_tier
    CHECK (source_tier IN ('A', 'B', 'C'))
);

CREATE INDEX IF NOT EXISTS idx_official_traction_entity_date
  ON official_traction_signals(entity_id, signal_type, signal_date DESC);
CREATE INDEX IF NOT EXISTS idx_official_traction_company_date
  ON official_traction_signals(company_id, signal_type, signal_date DESC);

CREATE TABLE IF NOT EXISTS company_source_raw (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  company_id uuid REFERENCES companies,
  entity_id uuid REFERENCES canonical_entities,
  source_name text NOT NULL,
  source_record_id text,
  source_timestamp timestamptz,
  source_tier text NOT NULL DEFAULT 'B',
  raw_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (source_name, source_record_id),
  CONSTRAINT chk_company_source_raw_tier
    CHECK (source_tier IN ('A', 'B', 'C'))
);

CREATE INDEX IF NOT EXISTS idx_company_source_raw_company
  ON company_source_raw(company_id, source_name, source_timestamp DESC);

ALTER TABLE valuation_scenario_audits
  ADD COLUMN IF NOT EXISTS valuation_gate_pass boolean,
  ADD COLUMN IF NOT EXISTS valuation_gate_reason text,
  ADD COLUMN IF NOT EXISTS stage_country_sector_comps integer,
  ADD COLUMN IF NOT EXISTS tier_a_share numeric,
  ADD COLUMN IF NOT EXISTS core_term_completeness numeric,
  ADD COLUMN IF NOT EXISTS valuation_term_conflicts integer;

CREATE INDEX IF NOT EXISTS idx_valuation_audits_gate
  ON valuation_scenario_audits(valuation_gate_pass, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_valuation_audits_gate_metrics
  ON valuation_scenario_audits(stage_country_sector_comps, tier_a_share, core_term_completeness);
