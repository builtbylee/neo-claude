-- 013_workbench_governance.sql
-- Analyst workflow, collaboration, CRM integration, alerting, and governance primitives.

CREATE TABLE IF NOT EXISTS deal_pipeline_items (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  evaluation_id uuid REFERENCES evaluations,
  entity_id uuid REFERENCES canonical_entities,
  company_name text NOT NULL,
  sector text,
  country text,
  stage_bucket text,
  recommendation_class text,
  conviction_score numeric,
  status text NOT NULL DEFAULT 'new',              -- new, screening, diligence, ic, pass, invest
  priority text NOT NULL DEFAULT 'medium',         -- low, medium, high, critical
  owner_email text NOT NULL,
  next_action_date date,
  source text DEFAULT 'manual',
  metadata jsonb DEFAULT '{}'::jsonb,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS diligence_tasks (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  deal_id uuid NOT NULL REFERENCES deal_pipeline_items ON DELETE CASCADE,
  title text NOT NULL,
  details text,
  status text NOT NULL DEFAULT 'open',             -- open, in_progress, blocked, done
  assignee_email text NOT NULL,
  due_date date,
  evidence_required boolean DEFAULT false,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS deal_comments (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  deal_id uuid NOT NULL REFERENCES deal_pipeline_items ON DELETE CASCADE,
  author_email text NOT NULL,
  body text NOT NULL,
  created_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS approval_requests (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  deal_id uuid NOT NULL REFERENCES deal_pipeline_items ON DELETE CASCADE,
  requested_by text NOT NULL,
  approver_email text NOT NULL,
  status text NOT NULL DEFAULT 'pending',          -- pending, approved, rejected
  decision_reason text,
  decided_at timestamptz,
  created_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS activity_events (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  actor_email text NOT NULL,
  entity_type text NOT NULL,                       -- deal, task, comment, approval, evaluation
  entity_id uuid NOT NULL,
  action text NOT NULL,                            -- created, updated, status_changed, approved, exported
  before_state jsonb,
  after_state jsonb,
  created_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS crm_links (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  deal_id uuid NOT NULL REFERENCES deal_pipeline_items ON DELETE CASCADE,
  provider text NOT NULL,                          -- airtable
  external_id text NOT NULL,
  sync_status text NOT NULL DEFAULT 'linked',      -- linked, stale, error
  last_synced_at timestamptz,
  raw_record jsonb,
  created_at timestamptz DEFAULT now(),
  UNIQUE (provider, external_id)
);

CREATE TABLE IF NOT EXISTS alert_rules (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_email text NOT NULL,
  enabled boolean DEFAULT true,
  priorities jsonb NOT NULL DEFAULT '["high","critical"]'::jsonb,
  quiet_start_hour smallint NOT NULL DEFAULT 22,
  quiet_end_hour smallint NOT NULL DEFAULT 7,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now(),
  UNIQUE (user_email)
);

CREATE TABLE IF NOT EXISTS alert_events (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  deal_id uuid REFERENCES deal_pipeline_items ON DELETE SET NULL,
  alert_type text NOT NULL,                        -- new_deal, status_change, risk_flag, task_due
  priority text NOT NULL DEFAULT 'medium',
  dedupe_key text NOT NULL,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  queued_at timestamptz DEFAULT now(),
  sent_at timestamptz,
  status text NOT NULL DEFAULT 'queued',           -- queued, sent, skipped, failed
  failure_reason text
);

CREATE TABLE IF NOT EXISTS data_retention_policy (
  id serial PRIMARY KEY,
  retention_days integer NOT NULL DEFAULT 365,
  pii_categories jsonb NOT NULL DEFAULT '["emails","phone_numbers","personal_profiles"]'::jsonb,
  active boolean DEFAULT true,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

-- Seed defaults for single-user setup.
INSERT INTO alert_rules (user_email)
SELECT 'owner@example.com'
WHERE NOT EXISTS (SELECT 1 FROM alert_rules);

INSERT INTO data_retention_policy (retention_days)
SELECT 365
WHERE NOT EXISTS (SELECT 1 FROM data_retention_policy);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_pipeline_status_priority
  ON deal_pipeline_items(status, priority, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_pipeline_owner
  ON deal_pipeline_items(owner_email, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_deal_status
  ON diligence_tasks(deal_id, status, due_date);
CREATE INDEX IF NOT EXISTS idx_comments_deal_created
  ON deal_comments(deal_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_approvals_deal_status
  ON approval_requests(deal_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_activity_entity
  ON activity_events(entity_type, entity_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_alert_events_queue
  ON alert_events(status, priority, queued_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_alert_dedupe_queued
  ON alert_events(dedupe_key)
  WHERE status IN ('queued', 'sent');
