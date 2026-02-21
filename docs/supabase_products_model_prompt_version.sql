-- Run this in Supabase Dashboard → SQL Editor
-- Adds model and prompt version tracking to products table.
-- Used to track which AI model and prompt produced each product's tags_ai_raw.

ALTER TABLE products ADD COLUMN IF NOT EXISTS model_version TEXT;
ALTER TABLE products ADD COLUMN IF NOT EXISTS prompt_version TEXT;

-- Optional index for filtering by model/prompt when analyzing training data
CREATE INDEX IF NOT EXISTS idx_products_model_version ON products(model_version) WHERE model_version IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_products_prompt_version ON products(prompt_version) WHERE prompt_version IS NOT NULL;
