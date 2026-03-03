-- 021_valuation_audit_matching_fields.sql
-- Add matching fields to valuation_scenario_audits for realized-outcome reconciliation.

ALTER TABLE valuation_scenario_audits
  ADD COLUMN IF NOT EXISTS company_name text,
  ADD COLUMN IF NOT EXISTS sector text,
  ADD COLUMN IF NOT EXISTS country text;

CREATE INDEX IF NOT EXISTS idx_valuation_audits_company_name
  ON valuation_scenario_audits(company_name);

CREATE INDEX IF NOT EXISTS idx_valuation_audits_sector_country
  ON valuation_scenario_audits(sector, country);
