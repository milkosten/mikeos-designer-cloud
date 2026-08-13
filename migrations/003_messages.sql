-- mikeos-designer-cloud: persist the per-project chat thread so a reload/reopen
-- restores the real prior conversation. Idempotent. No reserved-keyword columns.

ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS messages jsonb DEFAULT '[]'::jsonb;
