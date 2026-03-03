-- 014_auth_rls_and_model_governance.sql
-- Adds user roles, model health snapshots, and RLS policies for deal workflow tables.

CREATE TABLE IF NOT EXISTS user_roles (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  email text NOT NULL UNIQUE,
  role text NOT NULL DEFAULT 'analyst',               -- admin, analyst, viewer
  can_approve boolean NOT NULL DEFAULT false,
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now(),
  CONSTRAINT chk_user_roles_role CHECK (role IN ('admin', 'analyst', 'viewer'))
);

CREATE TABLE IF NOT EXISTS model_health_snapshots (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  model_version_id integer REFERENCES model_versions,
  backtest_run_id integer REFERENCES backtest_runs,
  snapshot_date date NOT NULL DEFAULT CURRENT_DATE,
  survival_auc numeric,
  calibration_ece numeric,
  portfolio_quality_vs_random numeric,
  release_gate_open boolean NOT NULL DEFAULT false,
  calibration_healthy boolean NOT NULL DEFAULT false,
  retrain_recommended boolean NOT NULL DEFAULT false,
  notes text,
  created_at timestamptz DEFAULT now()
);

INSERT INTO user_roles (email, role, can_approve, active)
SELECT 'owner@example.com', 'admin', true, true
WHERE NOT EXISTS (
  SELECT 1
  FROM user_roles
  WHERE lower(email) = lower('owner@example.com')
);

CREATE INDEX IF NOT EXISTS idx_user_roles_role_active
  ON user_roles(role, active);
CREATE INDEX IF NOT EXISTS idx_model_health_snapshots_date
  ON model_health_snapshots(snapshot_date DESC, created_at DESC);

CREATE OR REPLACE FUNCTION current_user_email()
RETURNS text
LANGUAGE sql
STABLE
AS $$
  SELECT lower(COALESCE(auth.jwt()->>'email', ''))
$$;

CREATE OR REPLACE FUNCTION has_write_role()
RETURNS boolean
LANGUAGE sql
STABLE
AS $$
  SELECT EXISTS (
    SELECT 1
    FROM user_roles ur
    WHERE lower(ur.email) = current_user_email()
      AND ur.active = true
      AND ur.role IN ('admin', 'analyst')
  )
$$;

CREATE OR REPLACE FUNCTION has_approve_role()
RETURNS boolean
LANGUAGE sql
STABLE
AS $$
  SELECT EXISTS (
    SELECT 1
    FROM user_roles ur
    WHERE lower(ur.email) = current_user_email()
      AND ur.active = true
      AND (ur.can_approve = true OR ur.role = 'admin')
  )
$$;

ALTER TABLE user_roles ENABLE ROW LEVEL SECURITY;
ALTER TABLE deal_pipeline_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE diligence_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE deal_comments ENABLE ROW LEVEL SECURITY;
ALTER TABLE approval_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE activity_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE alert_rules ENABLE ROW LEVEL SECURITY;
ALTER TABLE alert_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE model_health_snapshots ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'user_roles'
      AND policyname = 'user_roles_select_authenticated'
  ) THEN
    CREATE POLICY user_roles_select_authenticated
      ON user_roles
      FOR SELECT
      USING (auth.role() = 'authenticated');
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'user_roles'
      AND policyname = 'user_roles_manage_admin'
  ) THEN
    CREATE POLICY user_roles_manage_admin
      ON user_roles
      FOR ALL
      USING (has_approve_role())
      WITH CHECK (has_approve_role());
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'model_health_snapshots'
      AND policyname = 'model_health_select_authenticated'
  ) THEN
    CREATE POLICY model_health_select_authenticated
      ON model_health_snapshots
      FOR SELECT
      USING (auth.role() = 'authenticated');
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'model_health_snapshots'
      AND policyname = 'model_health_write_admin'
  ) THEN
    CREATE POLICY model_health_write_admin
      ON model_health_snapshots
      FOR ALL
      USING (has_approve_role())
      WITH CHECK (has_approve_role());
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'deal_pipeline_items'
      AND policyname = 'pipeline_select_authenticated'
  ) THEN
    CREATE POLICY pipeline_select_authenticated
      ON deal_pipeline_items
      FOR SELECT
      USING (auth.role() = 'authenticated');
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'deal_pipeline_items'
      AND policyname = 'pipeline_write_analyst'
  ) THEN
    CREATE POLICY pipeline_write_analyst
      ON deal_pipeline_items
      FOR ALL
      USING (has_write_role())
      WITH CHECK (has_write_role());
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'diligence_tasks'
      AND policyname = 'tasks_select_authenticated'
  ) THEN
    CREATE POLICY tasks_select_authenticated
      ON diligence_tasks
      FOR SELECT
      USING (auth.role() = 'authenticated');
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'diligence_tasks'
      AND policyname = 'tasks_write_analyst'
  ) THEN
    CREATE POLICY tasks_write_analyst
      ON diligence_tasks
      FOR ALL
      USING (has_write_role())
      WITH CHECK (has_write_role());
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'deal_comments'
      AND policyname = 'comments_select_authenticated'
  ) THEN
    CREATE POLICY comments_select_authenticated
      ON deal_comments
      FOR SELECT
      USING (auth.role() = 'authenticated');
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'deal_comments'
      AND policyname = 'comments_write_analyst'
  ) THEN
    CREATE POLICY comments_write_analyst
      ON deal_comments
      FOR ALL
      USING (has_write_role())
      WITH CHECK (has_write_role());
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'approval_requests'
      AND policyname = 'approvals_select_authenticated'
  ) THEN
    CREATE POLICY approvals_select_authenticated
      ON approval_requests
      FOR SELECT
      USING (auth.role() = 'authenticated');
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'approval_requests'
      AND policyname = 'approvals_write_analyst'
  ) THEN
    CREATE POLICY approvals_write_analyst
      ON approval_requests
      FOR INSERT
      WITH CHECK (has_write_role());
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'approval_requests'
      AND policyname = 'approvals_update_approver'
  ) THEN
    CREATE POLICY approvals_update_approver
      ON approval_requests
      FOR UPDATE
      USING (has_approve_role())
      WITH CHECK (has_approve_role());
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'activity_events'
      AND policyname = 'activity_select_authenticated'
  ) THEN
    CREATE POLICY activity_select_authenticated
      ON activity_events
      FOR SELECT
      USING (auth.role() = 'authenticated');
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'activity_events'
      AND policyname = 'activity_write_analyst'
  ) THEN
    CREATE POLICY activity_write_analyst
      ON activity_events
      FOR INSERT
      WITH CHECK (has_write_role());
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'crm_links'
      AND policyname = 'crm_select_authenticated'
  ) THEN
    CREATE POLICY crm_select_authenticated
      ON crm_links
      FOR SELECT
      USING (auth.role() = 'authenticated');
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'crm_links'
      AND policyname = 'crm_write_analyst'
  ) THEN
    CREATE POLICY crm_write_analyst
      ON crm_links
      FOR ALL
      USING (has_write_role())
      WITH CHECK (has_write_role());
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'alert_rules'
      AND policyname = 'alert_rules_select_authenticated'
  ) THEN
    CREATE POLICY alert_rules_select_authenticated
      ON alert_rules
      FOR SELECT
      USING (auth.role() = 'authenticated');
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'alert_rules'
      AND policyname = 'alert_rules_write_analyst'
  ) THEN
    CREATE POLICY alert_rules_write_analyst
      ON alert_rules
      FOR ALL
      USING (has_write_role())
      WITH CHECK (has_write_role());
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'alert_events'
      AND policyname = 'alert_events_select_authenticated'
  ) THEN
    CREATE POLICY alert_events_select_authenticated
      ON alert_events
      FOR SELECT
      USING (auth.role() = 'authenticated');
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'alert_events'
      AND policyname = 'alert_events_write_analyst'
  ) THEN
    CREATE POLICY alert_events_write_analyst
      ON alert_events
      FOR ALL
      USING (has_write_role())
      WITH CHECK (has_write_role());
  END IF;
END $$;

