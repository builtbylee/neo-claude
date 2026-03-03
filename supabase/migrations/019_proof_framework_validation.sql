-- 019_proof_framework_validation.sql
-- Proof framework tables for rolling segment validation, calibration curves,
-- valuation MAE cohorts, and quarterly evidence reporting.

CREATE TABLE IF NOT EXISTS backtest_window_results (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  backtest_run_id integer NOT NULL REFERENCES backtest_runs(id) ON DELETE CASCADE,
  segment_key text NOT NULL,
  window_label text NOT NULL,
  train_start date,
  train_end date,
  test_start date,
  test_end date,
  deals integer NOT NULL DEFAULT 0,
  labeled integer NOT NULL DEFAULT 0,
  survival_auc numeric,
  calibration_ece numeric,
  portfolio_failure_rate numeric,
  portfolio_quality numeric,
  failure_vs_random numeric,
  quality_vs_random numeric,
  progress_auc numeric,
  model_uncertainty_rate numeric,
  top_k_sector_concentration numeric,
  created_at timestamptz DEFAULT now(),
  CONSTRAINT chk_backtest_window_results_segment
    CHECK (segment_key IN ('US_Seed', 'US_EarlyGrowth', 'UK_Seed', 'UK_EarlyGrowth')),
  CONSTRAINT uq_backtest_window_results
    UNIQUE (backtest_run_id, segment_key, window_label)
);

CREATE INDEX IF NOT EXISTS idx_backtest_window_results_segment
  ON backtest_window_results(segment_key, test_end DESC);

CREATE INDEX IF NOT EXISTS idx_backtest_window_results_run
  ON backtest_window_results(backtest_run_id, window_label);

CREATE TABLE IF NOT EXISTS backtest_calibration_curves (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  backtest_run_id integer NOT NULL REFERENCES backtest_runs(id) ON DELETE CASCADE,
  segment_key text NOT NULL,
  window_label text NOT NULL,
  bin_index integer NOT NULL,
  bin_lower numeric NOT NULL,
  bin_upper numeric NOT NULL,
  sample_size integer NOT NULL DEFAULT 0,
  mean_pred numeric,
  observed_rate numeric,
  abs_error numeric,
  created_at timestamptz DEFAULT now(),
  CONSTRAINT chk_backtest_calibration_curves_segment
    CHECK (segment_key IN ('US_Seed', 'US_EarlyGrowth', 'UK_Seed', 'UK_EarlyGrowth')),
  CONSTRAINT uq_backtest_calibration_curve_bin
    UNIQUE (backtest_run_id, segment_key, window_label, bin_index)
);

CREATE INDEX IF NOT EXISTS idx_backtest_calibration_curves_segment
  ON backtest_calibration_curves(segment_key, window_label, bin_index);

CREATE TABLE IF NOT EXISTS valuation_cohort_mae (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  cohort_quarter date NOT NULL,
  segment_key text NOT NULL,
  valuation_confidence text NOT NULL,
  sample_size integer NOT NULL DEFAULT 0,
  mae numeric,
  mape numeric,
  coverage_ratio numeric,
  source_tier_mix jsonb NOT NULL DEFAULT '{}'::jsonb,
  computed_at timestamptz NOT NULL DEFAULT now(),
  notes text,
  CONSTRAINT chk_valuation_cohort_mae_segment
    CHECK (segment_key IN ('US_Seed', 'US_EarlyGrowth', 'UK_Seed', 'UK_EarlyGrowth')),
  CONSTRAINT chk_valuation_cohort_mae_confidence
    CHECK (valuation_confidence IN ('high', 'medium', 'low')),
  CONSTRAINT uq_valuation_cohort_mae
    UNIQUE (cohort_quarter, segment_key, valuation_confidence)
);

CREATE INDEX IF NOT EXISTS idx_valuation_cohort_mae_recent
  ON valuation_cohort_mae(cohort_quarter DESC, segment_key, valuation_confidence);

CREATE TABLE IF NOT EXISTS quarterly_evidence_reports (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  report_quarter date NOT NULL UNIQUE,
  generated_at timestamptz NOT NULL DEFAULT now(),
  run_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
  summary jsonb NOT NULL DEFAULT '{}'::jsonb,
  release_readiness boolean NOT NULL DEFAULT false,
  artifact_path text,
  notes text
);

CREATE INDEX IF NOT EXISTS idx_quarterly_evidence_reports_recent
  ON quarterly_evidence_reports(report_quarter DESC);
