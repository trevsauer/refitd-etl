-- Supabase Storage policies for product-images bucket
-- Run this in Supabase Dashboard → SQL Editor
-- https://supabase.com/dashboard/project/_/sql
--
-- The loader needs: list (SELECT), upload (INSERT), delete (DELETE).
-- If you already have "allow uploads and reads" in the UI, the missing piece is usually
-- SELECT so the Python client can list the bucket (which triggers the "bucket accessible" check).
--
-- If you get "policy already exists", run only the policy you need (e.g. just SELECT).
-- Run these one at a time if needed.

-- 0. Bucket visibility: some clients check storage.buckets before listing objects.
-- (If this fails with "column id does not exist", try: USING (name = 'product-images'))
CREATE POLICY "Allow anon to see product-images bucket"
ON storage.buckets FOR SELECT
TO anon
USING (id = 'product-images');

-- 1. SELECT: list objects + read (public URLs). Stops "Could not access bucket" warning.
CREATE POLICY "Allow list and read product-images"
ON storage.objects FOR SELECT
TO anon
USING (bucket_id = 'product-images');

-- 2. INSERT: upload images (you may already have this from UI).
CREATE POLICY "Allow upload product-images"
ON storage.objects FOR INSERT
TO anon
WITH CHECK (bucket_id = 'product-images');

-- 3. DELETE: remove images (for wipe).
CREATE POLICY "Allow delete product-images"
ON storage.objects FOR DELETE
TO anon
USING (bucket_id = 'product-images');

-- If you use the service_role key (SUPABASE_KEY = service key), it bypasses RLS and you won't see the warning.
-- If you use the anon key, the policies above must exist. If warning persists, try service_role key in .env.
