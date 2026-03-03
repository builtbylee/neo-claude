-- 023_synthetic_pilot_runs.sql
-- Persist synthetic analyst pilot runs and per-analyst deal decisions.

CREATE TABLE IF NOT EXISTS synthetic_pilot_runs (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  run_name text NOT NULL,
  cycle_id uuid NOT NULL REFERENCES shadow_cycles ON DELETE CASCADE,
  model_name text NOT NULL,
  max_items integer NOT NULL DEFAULT 25,
  started_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  summary jsonb,
  notes text
);

CREATE INDEX IF NOT EXISTS idx_synthetic_pilot_runs_cycle
  ON synthetic_pilot_runs(cycle_id, started_at DESC);

CREATE TABLE IF NOT EXISTS synthetic_pilot_decisions (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  run_id uuid NOT NULL REFERENCES synthetic_pilot_runs ON DELETE CASCADE,
  shadow_cycle_item_id uuid NOT NULL REFERENCES shadow_cycle_items ON DELETE CASCADE,
  analyst_profile text NOT NULL, -- conservative, balanced, aggressive
  recommendation_class text NOT NULL, -- invest, deep_diligence, watch, pass, abstain
  conviction numeric NOT NULL,
  rationale text NOT NULL,
  key_risks jsonb,
  data_gaps jsonb,
  raw_response jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (run_id, shadow_cycle_item_id, analyst_profile)
);

CREATE INDEX IF NOT EXISTS idx_synthetic_pilot_decisions_run
  ON synthetic_pilot_decisions(run_id, analyst_profile, created_at DESC);
