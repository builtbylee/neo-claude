-- 010_sec_cf_filings.sql
-- Reference table for per-filing metadata from SEC DERA CF datasets.
-- Used to derive outcome labels from submission types (C-TR, C-AR, etc.).

CREATE TABLE IF NOT EXISTS sec_cf_filings (
    id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    cik text NOT NULL,
    accession_number text NOT NULL,
    submission_type text NOT NULL,
    filing_date date,
    quarter text NOT NULL,  -- e.g. '2024Q4'
    created_at timestamptz DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_sec_cf_filings_accession
    ON sec_cf_filings (accession_number);

CREATE INDEX IF NOT EXISTS idx_sec_cf_filings_cik
    ON sec_cf_filings (cik);

CREATE INDEX IF NOT EXISTS idx_sec_cf_filings_type
    ON sec_cf_filings (submission_type);
