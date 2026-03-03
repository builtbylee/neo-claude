-- 017_rls_for_release_ops_tables.sql
-- Apply RLS and role-scoped policies for release ops tables.

ALTER TABLE user_subscriptions ENABLE ROW LEVEL SECURITY;
ALTER TABLE billing_plans ENABLE ROW LEVEL SECURITY;
ALTER TABLE usage_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE deal_reminders ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_exports ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'billing_plans'
      AND policyname = 'billing_plans_select_authenticated'
  ) THEN
    CREATE POLICY billing_plans_select_authenticated
      ON billing_plans
      FOR SELECT
      USING (auth.role() = 'authenticated');
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'billing_plans'
      AND policyname = 'billing_plans_write_admin'
  ) THEN
    CREATE POLICY billing_plans_write_admin
      ON billing_plans
      FOR ALL
      USING (has_approve_role())
      WITH CHECK (has_approve_role());
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'user_subscriptions'
      AND policyname = 'user_subscriptions_select_authenticated'
  ) THEN
    CREATE POLICY user_subscriptions_select_authenticated
      ON user_subscriptions
      FOR SELECT
      USING (auth.role() = 'authenticated');
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'user_subscriptions'
      AND policyname = 'user_subscriptions_write_admin'
  ) THEN
    CREATE POLICY user_subscriptions_write_admin
      ON user_subscriptions
      FOR ALL
      USING (has_approve_role())
      WITH CHECK (has_approve_role());
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'usage_events'
      AND policyname = 'usage_events_select_authenticated'
  ) THEN
    CREATE POLICY usage_events_select_authenticated
      ON usage_events
      FOR SELECT
      USING (auth.role() = 'authenticated');
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'usage_events'
      AND policyname = 'usage_events_write_analyst'
  ) THEN
    CREATE POLICY usage_events_write_analyst
      ON usage_events
      FOR INSERT
      WITH CHECK (has_write_role());
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'deal_reminders'
      AND policyname = 'deal_reminders_select_authenticated'
  ) THEN
    CREATE POLICY deal_reminders_select_authenticated
      ON deal_reminders
      FOR SELECT
      USING (auth.role() = 'authenticated');
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'deal_reminders'
      AND policyname = 'deal_reminders_write_analyst'
  ) THEN
    CREATE POLICY deal_reminders_write_analyst
      ON deal_reminders
      FOR ALL
      USING (has_write_role())
      WITH CHECK (has_write_role());
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'audit_exports'
      AND policyname = 'audit_exports_select_authenticated'
  ) THEN
    CREATE POLICY audit_exports_select_authenticated
      ON audit_exports
      FOR SELECT
      USING (auth.role() = 'authenticated');
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'audit_exports'
      AND policyname = 'audit_exports_write_analyst'
  ) THEN
    CREATE POLICY audit_exports_write_analyst
      ON audit_exports
      FOR INSERT
      WITH CHECK (has_write_role());
  END IF;
END $$;
