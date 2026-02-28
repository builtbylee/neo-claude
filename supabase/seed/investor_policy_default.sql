-- investor_policy_default.sql
-- Insert default investor policy row.

INSERT INTO investor_policy (
  max_investments_per_year,
  check_size,
  check_currency,
  max_per_sector_per_year,
  no_forced_deployment,
  compliance_hard_blocks,
  active,
  updated_at
) VALUES (
  2,          -- max 2 investments per year
  10000,      -- 10,000 GBP check size
  'GBP',
  1,          -- max 1 per sector per year
  true,       -- no forced deployment
  true,       -- compliance hard blocks enabled
  true,
  now()
)
ON CONFLICT DO NOTHING;
