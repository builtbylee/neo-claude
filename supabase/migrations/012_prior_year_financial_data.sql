-- Migration 012: Indexes for prior-year financial data and growth feature lookups.
--
-- The financial_data table stores both 'annual' (current FY) and 'prior_annual'
-- (prior FY) rows from DERA CF filings. These indexes support efficient joins
-- for computing YoY growth features and progress label construction.

CREATE INDEX IF NOT EXISTS idx_financial_data_company_period
    ON financial_data (company_id, period_end_date DESC);

CREATE INDEX IF NOT EXISTS idx_financial_data_period_type
    ON financial_data (period_type);
