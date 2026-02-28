-- 001_entity_resolution.sql
-- Enable uuid-ossp extension and create entity resolution tables.

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS canonical_entities (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  primary_name text NOT NULL,
  country text NOT NULL,
  sector text,
  founding_date date,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS entity_links (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  entity_id uuid REFERENCES canonical_entities NOT NULL,
  source text NOT NULL,             -- sec_edgar, companies_house, academic, manual
  source_identifier text NOT NULL,  -- CIK, company number, etc.
  source_name text,                 -- name as it appears in this source
  match_method text NOT NULL,       -- exact_id, deterministic, probabilistic
  confidence integer NOT NULL,      -- 0-100
  review_status text DEFAULT 'auto_confirmed',  -- auto_confirmed, needs_review, rejected
  created_at timestamptz DEFAULT now()
);
