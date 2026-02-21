-- Run this in Supabase Dashboard → SQL Editor
-- Adds curation tracking columns to products table for training data readiness.

-- Curation tracking (when product was last curated, by whom, version)
ALTER TABLE products ADD COLUMN IF NOT EXISTS curated_at TIMESTAMPTZ;
ALTER TABLE products ADD COLUMN IF NOT EXISTS curated_by TEXT;
ALTER TABLE products ADD COLUMN IF NOT EXISTS curation_version INTEGER DEFAULT 1;

-- Whether product should be included in training data
ALTER TABLE products ADD COLUMN IF NOT EXISTS training_eligible BOOLEAN DEFAULT false;

-- Index for fast queries (filter training-eligible products)
CREATE INDEX IF NOT EXISTS idx_products_training_eligible ON products(training_eligible) WHERE training_eligible = true;
