-- 005_backtesting.sql
-- Create backtesting tables for holdout data and run tracking.

CREATE TABLE IF NOT EXISTS backtest_holdout (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  entity_id uuid REFERENCES canonical_entities,
  company_id uuid REFERENCES companies,
  holdout_window text NOT NULL,     -- e.g., "2023-2025"
  created_at timestamptz DEFAULT now()
  -- This table is physically separated from training pipeline access.
  -- Records are created once before model training and never modified.
);

CREATE TABLE IF NOT EXISTS backtest_runs (
  id serial PRIMARY KEY,
  run_date timestamptz DEFAULT now(),
  model_family text,
  model_version_id integer REFERENCES model_versions,
  data_snapshot_date date,
  train_window text,
  test_window text,
  features_active jsonb,
  alt_data_signals_included jsonb,  -- which of the 8 reconstructable signals were used
  metrics jsonb,                    -- {auc, ece, portfolio_moic, failure_rate, ...}
  baselines jsonb,                  -- {random: {...}, heuristic: {...}, momentum: {...}}
  pass_fail jsonb,                  -- {metric_name: {value, threshold, passed}}
  all_passed boolean,
  notes text
);
