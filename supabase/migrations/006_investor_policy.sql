-- 006_investor_policy.sql
-- Create investor policy configuration table.

CREATE TABLE IF NOT EXISTS investor_policy (
  id serial PRIMARY KEY,
  max_investments_per_year integer DEFAULT 2,
  check_size numeric DEFAULT 10000,
  check_currency text DEFAULT 'GBP',
  max_per_sector_per_year integer DEFAULT 1,
  no_forced_deployment boolean DEFAULT true,
  compliance_hard_blocks boolean DEFAULT true,
  active boolean DEFAULT true,
  updated_at timestamptz DEFAULT now()
);
