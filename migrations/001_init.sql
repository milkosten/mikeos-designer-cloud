-- mikeos-designer-cloud schema. Idempotent. No reserved-keyword columns.
CREATE TABLE IF NOT EXISTS projects (
    id             char(6)      PRIMARY KEY,     -- base62 site id, also the public folder name
    user_id        text         NOT NULL,
    title          text,
    page_type      text,
    style          text,
    prompt         text,                          -- the original request
    prompt_history jsonb        NOT NULL DEFAULT '[]',   -- [{instruction, at}] refinements
    pages          jsonb        NOT NULL DEFAULT '[]',   -- [{file, html}]
    visibility     text         NOT NULL DEFAULT 'unlisted',
    published      boolean      NOT NULL DEFAULT true,
    created_at     timestamptz  NOT NULL DEFAULT now(),
    updated_at     timestamptz  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS projects_user_id_idx ON projects (user_id);
CREATE INDEX IF NOT EXISTS projects_updated_at_idx ON projects (updated_at DESC);
