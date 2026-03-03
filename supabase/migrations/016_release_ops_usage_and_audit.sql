-- 016_release_ops_usage_and_audit.sql
-- Release hardening: migration ledger, usage limits, reminders, and audit exports.

CREATE TABLE IF NOT EXISTS schema_migrations_startuplens (
  version text PRIMARY KEY,
  checksum text NOT NULL,
  applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS billing_plans (
  id serial PRIMARY KEY,
  plan_code text NOT NULL UNIQUE,
  display_name text NOT NULL,
  monthly_quick_limit integer NOT NULL DEFAULT 500,
  monthly_batch_limit integer NOT NULL DEFAULT 100,
  monthly_export_limit integer NOT NULL DEFAULT 100,
  hard_block_on_limit boolean NOT NULL DEFAULT true,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

INSERT INTO billing_plans (
  plan_code,
  display_name,
  monthly_quick_limit,
  monthly_batch_limit,
  monthly_export_limit,
  hard_block_on_limit
)
VALUES
  ('free', 'Free', 300, 80, 80, true),
  ('pro', 'Pro', 3000, 600, 600, false)
ON CONFLICT (plan_code) DO NOTHING;

CREATE TABLE IF NOT EXISTS user_subscriptions (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  email text NOT NULL UNIQUE,
  plan_code text NOT NULL REFERENCES billing_plans(plan_code) DEFAULT 'free',
  status text NOT NULL DEFAULT 'active',
  period_start date NOT NULL DEFAULT date_trunc('month', CURRENT_DATE)::date,
  period_end date NOT NULL DEFAULT (date_trunc('month', CURRENT_DATE) + INTERVAL '1 month - 1 day')::date,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now(),
  CONSTRAINT chk_user_subscriptions_status
    CHECK (status IN ('active', 'past_due', 'paused', 'cancelled'))
);

CREATE TABLE IF NOT EXISTS usage_events (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  email text NOT NULL,
  usage_type text NOT NULL,
  units integer NOT NULL DEFAULT 1,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz DEFAULT now(),
  CONSTRAINT chk_usage_events_type
    CHECK (usage_type IN ('quick_score', 'batch_score', 'memo_export', 'audit_export'))
);

CREATE INDEX IF NOT EXISTS idx_usage_events_email_type_month
  ON usage_events(email, usage_type, created_at DESC);

CREATE TABLE IF NOT EXISTS deal_reminders (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  deal_id uuid NOT NULL REFERENCES deal_pipeline_items ON DELETE CASCADE,
  reminder_type text NOT NULL,
  due_at timestamptz NOT NULL,
  priority text NOT NULL DEFAULT 'medium',
  status text NOT NULL DEFAULT 'pending',
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_by text NOT NULL,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now(),
  CONSTRAINT chk_deal_reminders_type
    CHECK (reminder_type IN ('next_action', 'follow_on', 'diligence_review')),
  CONSTRAINT chk_deal_reminders_priority
    CHECK (priority IN ('low', 'medium', 'high', 'critical')),
  CONSTRAINT chk_deal_reminders_status
    CHECK (status IN ('pending', 'sent', 'done', 'cancelled'))
);

CREATE INDEX IF NOT EXISTS idx_deal_reminders_due
  ON deal_reminders(status, due_at, priority);

CREATE TABLE IF NOT EXISTS audit_exports (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  requested_by text NOT NULL,
  export_type text NOT NULL,
  format text NOT NULL DEFAULT 'json',
  record_count integer NOT NULL DEFAULT 0,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz DEFAULT now(),
  CONSTRAINT chk_audit_exports_type
    CHECK (export_type IN ('activity_events', 'recommendations', 'pipeline_snapshot'))
);

CREATE INDEX IF NOT EXISTS idx_audit_exports_requested
  ON audit_exports(requested_by, created_at DESC);
