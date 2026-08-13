-- mikeos-designer-cloud: editable brief, opt-in interactivity, thumbnails, version history.
-- Idempotent. No reserved-keyword columns.

ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS brief       jsonb   DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS interactive boolean DEFAULT false,
    ADD COLUMN IF NOT EXISTS thumbnail   text;

-- One immutable snapshot of the pages/brief per generate/edit/brief/restyle, so a project
-- can be reverted to any earlier state. `note` describes what produced the snapshot.
CREATE TABLE IF NOT EXISTS project_versions (
    version_id  bigserial   PRIMARY KEY,
    project_id  char(6)     NOT NULL,
    pages       jsonb       NOT NULL,
    brief       jsonb,
    note        text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS project_versions_project_id_idx
    ON project_versions (project_id, version_id DESC);
