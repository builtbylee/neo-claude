-- 009_companies_unique_source.sql
-- Add missing UNIQUE constraints that ON CONFLICT upserts depend on.

-- Partial unique index on companies(source, source_id).
-- All pipelines use ON CONFLICT (source, source_id) WHERE source_id IS NOT NULL.
-- Note: may already exist as idx_companies_source from earlier setup.
CREATE UNIQUE INDEX IF NOT EXISTS idx_companies_source
    ON companies (source, source_id)
    WHERE source_id IS NOT NULL;

-- Unique index on entity_links(source, source_identifier).
-- Prevents duplicate links from bulk_create_entities() or re-runs.
CREATE UNIQUE INDEX IF NOT EXISTS idx_entity_links_source_identifier
    ON entity_links (source, source_identifier);
