-- Run this in Supabase Dashboard → SQL Editor
-- Creates curation_history table for storing manual corrections during product curation.
-- Used as training data for fine-tuning the AI tagger.
--
-- Error type values (for error_types array):
--   overtagging, undertagging, wrong_style_identity, wrong_construction,
--   wrong_formality, wrong_fit, wrong_color_interpretation, ambiguous_product,
--   other

CREATE TABLE IF NOT EXISTS curation_history (
    id SERIAL PRIMARY KEY,
    product_id TEXT NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,

    -- Tag snapshots (at time of curation)
    original_ai_tags JSONB NOT NULL,   -- Snapshot of tags_ai_raw (what the model predicted)
    corrected_tags JSONB NOT NULL,     -- Snapshot of tags_final (what the curator changed to)

    -- Change description
    change_summary TEXT,               -- Human-readable: "Added 'minimal' to style_identity; removed 'athletic' from formality"

    -- Curator context
    curator_notes TEXT,                -- Free text: why changes were made
    error_types TEXT[] DEFAULT '{}',   -- Array: overtagging, undertagging, wrong_style_identity, wrong_construction, etc.

    -- Training data quality
    confidence_in_correction SMALLINT CHECK (confidence_in_correction BETWEEN 1 AND 5),  -- 1=low, 5=high
    include_in_training BOOLEAN DEFAULT true,

    -- Metadata
    curator_id TEXT NOT NULL,
    model_version TEXT,                -- AI model version that produced original_ai_tags
    prompt_version TEXT,               -- Tagging prompt version used
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for efficient querying of training data
CREATE INDEX IF NOT EXISTS idx_curation_history_product_id ON curation_history(product_id);
CREATE INDEX IF NOT EXISTS idx_curation_history_include_in_training ON curation_history(include_in_training) WHERE include_in_training = true;
CREATE INDEX IF NOT EXISTS idx_curation_history_curator_id ON curation_history(curator_id);
CREATE INDEX IF NOT EXISTS idx_curation_history_created_at ON curation_history(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_curation_history_model_version ON curation_history(model_version) WHERE model_version IS NOT NULL;

-- GIN index for error_types array (query by error type)
CREATE INDEX IF NOT EXISTS idx_curation_history_error_types ON curation_history USING GIN (error_types);

-- Composite index for training export: include_in_training + created_at
CREATE INDEX IF NOT EXISTS idx_curation_history_training_export ON curation_history(include_in_training, created_at DESC) WHERE include_in_training = true;

-- Enable RLS
ALTER TABLE curation_history ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all access to curation_history" ON curation_history;
CREATE POLICY "Allow all access to curation_history" ON curation_history
    FOR ALL
    USING (true)
    WITH CHECK (true);

-- Optional view: training-ready corrections (for export)
CREATE OR REPLACE VIEW curation_history_training_export AS
SELECT
    ch.id,
    ch.product_id,
    p.name AS product_name,
    p.category,
    p.description,
    ch.original_ai_tags,
    ch.corrected_tags,
    ch.change_summary,
    ch.curator_notes,
    ch.error_types,
    ch.confidence_in_correction,
    ch.curator_id,
    ch.model_version,
    ch.prompt_version,
    ch.created_at
FROM curation_history ch
JOIN products p ON ch.product_id = p.product_id
WHERE ch.include_in_training = true
ORDER BY ch.created_at DESC;
