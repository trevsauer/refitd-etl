-- ============================================
-- REFITD ETL - SECURE RLS POLICIES
-- ============================================
-- Run this in Supabase Dashboard → SQL Editor
-- https://supabase.com/dashboard/project/_/sql
--
-- These policies restrict access properly:
-- - Service role key: Full access (for ETL pipeline)
-- - Anon key: Read-only access (for viewer/UI)
-- - Authenticated users: Full access (for curation)
--
-- ⚠️ IMPORTANT: Run these AFTER creating the tables
-- ============================================

-- ============================================
-- PRODUCTS TABLE
-- ============================================

-- First, drop the overly permissive policy
DROP POLICY IF EXISTS "Allow all access" ON products;

-- Service role bypasses RLS automatically, so these policies are for anon/authenticated

-- Anon users: Read-only access
CREATE POLICY "Anon read products"
ON products FOR SELECT
TO anon
USING (true);

-- Authenticated users: Full CRUD access
CREATE POLICY "Authenticated full access products"
ON products FOR ALL
TO authenticated
USING (true)
WITH CHECK (true);

-- ============================================
-- CURATION_HISTORY TABLE
-- ============================================

DROP POLICY IF EXISTS "Allow all access to curation_history" ON curation_history;

-- Anon users: Read-only (for training export)
CREATE POLICY "Anon read curation_history"
ON curation_history FOR SELECT
TO anon
USING (true);

-- Authenticated users: Full access (for curators)
CREATE POLICY "Authenticated full access curation_history"
ON curation_history FOR ALL
TO authenticated
USING (true)
WITH CHECK (true);

-- ============================================
-- CURATED_METADATA TABLE
-- ============================================

DROP POLICY IF EXISTS "Allow all access to curated_metadata" ON curated_metadata;

-- Anon users: Read-only
CREATE POLICY "Anon read curated_metadata"
ON curated_metadata FOR SELECT
TO anon
USING (true);

-- Authenticated users: Full access
CREATE POLICY "Authenticated full access curated_metadata"
ON curated_metadata FOR ALL
TO authenticated
USING (true)
WITH CHECK (true);

-- ============================================
-- CURATION_STATUS TABLE
-- ============================================

DROP POLICY IF EXISTS "Allow all access to curation_status" ON curation_status;

-- Anon users: Read-only
CREATE POLICY "Anon read curation_status"
ON curation_status FOR SELECT
TO anon
USING (true);

-- Authenticated users: Full access
CREATE POLICY "Authenticated full access curation_status"
ON curation_status FOR ALL
TO authenticated
USING (true)
WITH CHECK (true);

-- ============================================
-- REJECTED_INFERRED_TAGS TABLE
-- ============================================

DROP POLICY IF EXISTS "Allow all access to rejected_inferred_tags" ON rejected_inferred_tags;

-- Anon users: Read-only
CREATE POLICY "Anon read rejected_inferred_tags"
ON rejected_inferred_tags FOR SELECT
TO anon
USING (true);

-- Authenticated users: Full access
CREATE POLICY "Authenticated full access rejected_inferred_tags"
ON rejected_inferred_tags FOR ALL
TO authenticated
USING (true)
WITH CHECK (true);

-- ============================================
-- AI_GENERATED_TAGS TABLE
-- ============================================

DROP POLICY IF EXISTS "Allow all access to ai_generated_tags" ON ai_generated_tags;

-- Anon users: Read-only
CREATE POLICY "Anon read ai_generated_tags"
ON ai_generated_tags FOR SELECT
TO anon
USING (true);

-- Authenticated users: Full access
CREATE POLICY "Authenticated full access ai_generated_tags"
ON ai_generated_tags FOR ALL
TO authenticated
USING (true)
WITH CHECK (true);

-- ============================================
-- CUSTOM_VOCABULARY TABLE
-- ============================================

DROP POLICY IF EXISTS "Allow all access to custom_vocabulary" ON custom_vocabulary;

-- Anon users: Read-only
CREATE POLICY "Anon read custom_vocabulary"
ON custom_vocabulary FOR SELECT
TO anon
USING (true);

-- Authenticated users: Full access
CREATE POLICY "Authenticated full access custom_vocabulary"
ON custom_vocabulary FOR ALL
TO authenticated
USING (true)
WITH CHECK (true);

-- ============================================
-- VERIFICATION
-- ============================================
-- Run this to verify RLS is enabled and policies exist:

SELECT
    schemaname,
    tablename,
    policyname,
    permissive,
    roles,
    cmd,
    qual
FROM pg_policies
WHERE schemaname = 'public'
ORDER BY tablename, policyname;

-- ============================================
-- NOTES ON ACCESS PATTERNS
-- ============================================
-- 
-- ETL Pipeline (uses SUPABASE_KEY = service_role):
--   → Bypasses RLS completely
--   → Can INSERT, UPDATE, DELETE products
--   → Can run wipe_database.py
--
-- Viewer UI (uses anon key):
--   → Read-only access to all tables
--   → Cannot modify data
--
-- Curation UI (uses authenticated session):
--   → Full access after login
--   → Can update products, create curation_history records
--
-- To test unauthorized access:
--   1. Use anon key in .env
--   2. Try to INSERT into products → should fail
--   3. Try to SELECT from products → should succeed
--
-- ============================================
