-- Run this in Supabase Dashboard → SQL Editor
-- Fixes: "Could not find the 'color' column" and "Could not find the 'curation_status_refitd' column"

-- Product save (loader)
ALTER TABLE products ADD COLUMN IF NOT EXISTS brand_name TEXT;  -- Hard filter for generator (e.g. Zara)
ALTER TABLE products ADD COLUMN IF NOT EXISTS color TEXT;
ALTER TABLE products ADD COLUMN IF NOT EXISTS parent_product_id TEXT;
ALTER TABLE products ADD COLUMN IF NOT EXISTS composition TEXT;
ALTER TABLE products ADD COLUMN IF NOT EXISTS composition_structured JSONB;
ALTER TABLE products ADD COLUMN IF NOT EXISTS sizes_availability JSONB DEFAULT '[]';
ALTER TABLE products ADD COLUMN IF NOT EXISTS sizes_checked_at TIMESTAMPTZ;
ALTER TABLE products ADD COLUMN IF NOT EXISTS weight JSONB;
ALTER TABLE products ADD COLUMN IF NOT EXISTS style_tags JSONB;
ALTER TABLE products ADD COLUMN IF NOT EXISTS formality JSONB;

-- AI tagging (pipeline)
ALTER TABLE products ADD COLUMN IF NOT EXISTS tags_ai_raw JSONB;
ALTER TABLE products ADD COLUMN IF NOT EXISTS tags_final JSONB;
ALTER TABLE products ADD COLUMN IF NOT EXISTS curation_status_refitd TEXT DEFAULT 'pending';
ALTER TABLE products ADD COLUMN IF NOT EXISTS tag_policy_version TEXT;

-- Indexes (optional, for faster queries)
CREATE INDEX IF NOT EXISTS idx_products_parent_product_id ON products(parent_product_id);
CREATE INDEX IF NOT EXISTS idx_products_color ON products(color);
CREATE INDEX IF NOT EXISTS idx_products_curation_status ON products(curation_status_refitd);
CREATE INDEX IF NOT EXISTS idx_products_tags_final ON products USING GIN (tags_final);
CREATE INDEX IF NOT EXISTS idx_products_brand_name ON products(brand_name);

-- ReFitd canonical slots (for outfit generator)
ALTER TABLE products ADD COLUMN IF NOT EXISTS category_refitd TEXT;
ALTER TABLE products ADD COLUMN IF NOT EXISTS top_layer_role TEXT;
CREATE INDEX IF NOT EXISTS idx_products_category_refitd ON products(category_refitd);
CREATE INDEX IF NOT EXISTS idx_products_top_layer_role ON products(top_layer_role);
