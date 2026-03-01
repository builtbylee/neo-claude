-- Scraped narrative text from EDGAR Form C filings
CREATE TABLE sec_form_c_texts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id uuid NOT NULL REFERENCES companies(id),
    cik text NOT NULL,
    accession_number text NOT NULL,
    filing_date date,
    narrative_text text NOT NULL,
    source_sections jsonb,
    text_length integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (company_id, accession_number)
);

CREATE INDEX idx_form_c_texts_cik ON sec_form_c_texts (cik);

-- Claude text scores: 7 dimensions + aggregate
CREATE TABLE claude_text_scores (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id uuid NOT NULL REFERENCES companies(id),
    form_c_text_id uuid NOT NULL REFERENCES sec_form_c_texts(id),
    clarity smallint NOT NULL CHECK (clarity BETWEEN 0 AND 100),
    claims_plausibility smallint NOT NULL CHECK (claims_plausibility BETWEEN 0 AND 100),
    problem_specificity smallint NOT NULL CHECK (problem_specificity BETWEEN 0 AND 100),
    differentiation_depth smallint NOT NULL CHECK (differentiation_depth BETWEEN 0 AND 100),
    founder_domain_signal smallint NOT NULL CHECK (founder_domain_signal BETWEEN 0 AND 100),
    risk_honesty smallint NOT NULL CHECK (risk_honesty BETWEEN 0 AND 100),
    business_model_clarity smallint NOT NULL CHECK (business_model_clarity BETWEEN 0 AND 100),
    text_quality_score smallint NOT NULL CHECK (text_quality_score BETWEEN 0 AND 100),
    red_flags jsonb,
    reasoning text,
    prompt_version text NOT NULL,
    model_id text NOT NULL,
    raw_response jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (company_id, prompt_version)
);

CREATE INDEX idx_text_scores_company ON claude_text_scores (company_id);
