-- 004_model_registry.sql
-- Create model registry and rubric versioning tables.

CREATE TABLE IF NOT EXISTS model_versions (
  id serial PRIMARY KEY,
  family text NOT NULL,             -- UK_Seed, UK_EarlyGrowth, US_Seed, US_EarlyGrowth
  model_type text NOT NULL,         -- survival, progress
  version text NOT NULL,
  trained_at timestamptz NOT NULL,
  training_samples integer,
  tier1_samples integer,
  tier2_samples integer,
  validation_method text,
  validation_auc numeric,
  test_auc numeric,
  calibration_ece numeric,
  feature_importance jsonb,         -- SHAP rankings
  hyperparameters jsonb,
  artifact_path text,               -- path to serialised model
  release_status text DEFAULT 'candidate',  -- candidate, released, retired
  notes text
);

CREATE TABLE IF NOT EXISTS rubric_versions (
  id serial PRIMARY KEY,
  version text NOT NULL,
  generated_at timestamptz NOT NULL,
  model_versions jsonb,             -- [model_version_ids used]
  model_a_summary text,
  validation_method text,
  category_weights jsonb,
  scoring_thresholds jsonb,
  academic_overrides jsonb,
  prompt_versions jsonb,            -- {stage1: hash, stage2: hash, stage3: hash}
  notes text
);
