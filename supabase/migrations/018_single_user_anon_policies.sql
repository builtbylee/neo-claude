-- 018_single_user_anon_policies.sql
-- Enable single-user mode without OAuth by allowing anon API access
-- to workflow tables. Intended for personal deployments.

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'deal_pipeline_items'
      AND policyname = 'pipeline_select_anon'
  ) THEN
    CREATE POLICY pipeline_select_anon
      ON deal_pipeline_items
      FOR SELECT
      USING (auth.role() = 'anon');
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'deal_pipeline_items'
      AND policyname = 'pipeline_write_anon'
  ) THEN
    CREATE POLICY pipeline_write_anon
      ON deal_pipeline_items
      FOR ALL
      USING (auth.role() = 'anon')
      WITH CHECK (auth.role() = 'anon');
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'diligence_tasks'
      AND policyname = 'tasks_select_anon'
  ) THEN
    CREATE POLICY tasks_select_anon
      ON diligence_tasks
      FOR SELECT
      USING (auth.role() = 'anon');
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'diligence_tasks'
      AND policyname = 'tasks_write_anon'
  ) THEN
    CREATE POLICY tasks_write_anon
      ON diligence_tasks
      FOR ALL
      USING (auth.role() = 'anon')
      WITH CHECK (auth.role() = 'anon');
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'deal_comments'
      AND policyname = 'comments_select_anon'
  ) THEN
    CREATE POLICY comments_select_anon
      ON deal_comments
      FOR SELECT
      USING (auth.role() = 'anon');
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'deal_comments'
      AND policyname = 'comments_write_anon'
  ) THEN
    CREATE POLICY comments_write_anon
      ON deal_comments
      FOR ALL
      USING (auth.role() = 'anon')
      WITH CHECK (auth.role() = 'anon');
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'approval_requests'
      AND policyname = 'approvals_select_anon'
  ) THEN
    CREATE POLICY approvals_select_anon
      ON approval_requests
      FOR SELECT
      USING (auth.role() = 'anon');
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'approval_requests'
      AND policyname = 'approvals_write_anon'
  ) THEN
    CREATE POLICY approvals_write_anon
      ON approval_requests
      FOR ALL
      USING (auth.role() = 'anon')
      WITH CHECK (auth.role() = 'anon');
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'activity_events'
      AND policyname = 'activity_select_anon'
  ) THEN
    CREATE POLICY activity_select_anon
      ON activity_events
      FOR SELECT
      USING (auth.role() = 'anon');
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'activity_events'
      AND policyname = 'activity_write_anon'
  ) THEN
    CREATE POLICY activity_write_anon
      ON activity_events
      FOR INSERT
      WITH CHECK (auth.role() = 'anon');
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'deal_reminders'
      AND policyname = 'deal_reminders_select_anon'
  ) THEN
    CREATE POLICY deal_reminders_select_anon
      ON deal_reminders
      FOR SELECT
      USING (auth.role() = 'anon');
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'deal_reminders'
      AND policyname = 'deal_reminders_write_anon'
  ) THEN
    CREATE POLICY deal_reminders_write_anon
      ON deal_reminders
      FOR ALL
      USING (auth.role() = 'anon')
      WITH CHECK (auth.role() = 'anon');
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'user_subscriptions'
      AND policyname = 'user_subscriptions_select_anon'
  ) THEN
    CREATE POLICY user_subscriptions_select_anon
      ON user_subscriptions
      FOR SELECT
      USING (auth.role() = 'anon');
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'user_subscriptions'
      AND policyname = 'user_subscriptions_write_anon'
  ) THEN
    CREATE POLICY user_subscriptions_write_anon
      ON user_subscriptions
      FOR ALL
      USING (auth.role() = 'anon')
      WITH CHECK (auth.role() = 'anon');
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'usage_events'
      AND policyname = 'usage_events_select_anon'
  ) THEN
    CREATE POLICY usage_events_select_anon
      ON usage_events
      FOR SELECT
      USING (auth.role() = 'anon');
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'usage_events'
      AND policyname = 'usage_events_write_anon'
  ) THEN
    CREATE POLICY usage_events_write_anon
      ON usage_events
      FOR ALL
      USING (auth.role() = 'anon')
      WITH CHECK (auth.role() = 'anon');
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'billing_plans'
      AND policyname = 'billing_plans_select_anon'
  ) THEN
    CREATE POLICY billing_plans_select_anon
      ON billing_plans
      FOR SELECT
      USING (auth.role() = 'anon');
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'audit_exports'
      AND policyname = 'audit_exports_select_anon'
  ) THEN
    CREATE POLICY audit_exports_select_anon
      ON audit_exports
      FOR SELECT
      USING (auth.role() = 'anon');
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'audit_exports'
      AND policyname = 'audit_exports_write_anon'
  ) THEN
    CREATE POLICY audit_exports_write_anon
      ON audit_exports
      FOR INSERT
      WITH CHECK (auth.role() = 'anon');
  END IF;
END $$;
