-- 022_shadow_cycles_and_threshold_freeze.sql
-- Add persistent shadow-cycle tracking and threshold-freeze governance.

CREATE TABLE IF NOT EXISTS threshold_freezes (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  quarter_label text NOT NULL,
  freeze_note text,
  files jsonb NOT NULL,
  frozen_by text NOT NULL DEFAULT 'system',
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_threshold_freezes_recent
  ON threshold_freezes(created_at DESC);

CREATE TABLE IF NOT EXISTS shadow_cycles (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  cycle_name text NOT NULL,
  target_count integer NOT NULL DEFAULT 25,
  status text NOT NULL DEFAULT 'active', -- active, completed, cancelled
  policy_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
  threshold_freeze_id uuid REFERENCES threshold_freezes,
  started_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  notes text
);

CREATE INDEX IF NOT EXISTS idx_shadow_cycles_status
  ON shadow_cycles(status, started_at DESC);

CREATE TABLE IF NOT EXISTS shadow_cycle_items (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  cycle_id uuid NOT NULL REFERENCES shadow_cycles ON DELETE CASCADE,
  entity_id uuid REFERENCES canonical_entities,
  company_name text NOT NULL,
  sector text,
  country text,
  source text NOT NULL,
  source_ref text,
  created_at timestamptz NOT NULL DEFAULT now(),
  evaluation_id uuid REFERENCES evaluations,
  recommendation_class text,
  outcome text NOT NULL DEFAULT 'unknown',
  outcome_updated_at timestamptz,
  notes text,
  UNIQUE (cycle_id, company_name, source, source_ref)
);

CREATE INDEX IF NOT EXISTS idx_shadow_cycle_items_cycle
  ON shadow_cycle_items(cycle_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_shadow_cycle_items_outcome
  ON shadow_cycle_items(cycle_id, outcome);
