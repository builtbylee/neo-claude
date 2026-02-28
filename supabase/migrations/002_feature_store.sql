-- 002_feature_store.sql
-- Create the as-of feature store table.

CREATE TABLE IF NOT EXISTS feature_store (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  entity_id uuid REFERENCES canonical_entities NOT NULL,
  as_of_date date NOT NULL,
  feature_family text NOT NULL,     -- campaign, company, team, financial, traction,
                                    --   terms, regulatory, market_regime, evidence
  feature_name text NOT NULL,
  feature_value jsonb NOT NULL,
  source text NOT NULL,
  label_quality_tier smallint NOT NULL,  -- 1=verified, 2=estimated, 3=weak
  created_at timestamptz DEFAULT now(),
  UNIQUE (entity_id, as_of_date, feature_family, feature_name)
);
