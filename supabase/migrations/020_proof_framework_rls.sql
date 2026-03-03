-- 020_proof_framework_rls.sql
-- RLS for proof-framework tables used by model health and evidence APIs.

ALTER TABLE backtest_window_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE backtest_calibration_curves ENABLE ROW LEVEL SECURITY;
ALTER TABLE valuation_cohort_mae ENABLE ROW LEVEL SECURITY;
ALTER TABLE quarterly_evidence_reports ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'backtest_window_results'
      AND policyname = 'backtest_window_results_select_anon'
  ) THEN
    CREATE POLICY backtest_window_results_select_anon
      ON backtest_window_results
      FOR SELECT
      USING (auth.role() = 'anon' OR auth.role() = 'authenticated');
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'backtest_calibration_curves'
      AND policyname = 'backtest_calibration_curves_select_anon'
  ) THEN
    CREATE POLICY backtest_calibration_curves_select_anon
      ON backtest_calibration_curves
      FOR SELECT
      USING (auth.role() = 'anon' OR auth.role() = 'authenticated');
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'valuation_cohort_mae'
      AND policyname = 'valuation_cohort_mae_select_anon'
  ) THEN
    CREATE POLICY valuation_cohort_mae_select_anon
      ON valuation_cohort_mae
      FOR SELECT
      USING (auth.role() = 'anon' OR auth.role() = 'authenticated');
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'quarterly_evidence_reports'
      AND policyname = 'quarterly_evidence_reports_select_anon'
  ) THEN
    CREATE POLICY quarterly_evidence_reports_select_anon
      ON quarterly_evidence_reports
      FOR SELECT
      USING (auth.role() = 'anon' OR auth.role() = 'authenticated');
  END IF;
END $$;
