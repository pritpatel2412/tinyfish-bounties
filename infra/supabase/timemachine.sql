-- infra/supabase/timemachine.sql
-- Schema for Time Machine: Web Surface Intelligence & As-Of Auditor

CREATE TABLE IF NOT EXISTS pages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    url TEXT UNIQUE NOT NULL,
    title TEXT,
    category TEXT,
    monitor_id TEXT,
    schedule_cron TEXT,
    interval_label TEXT,
    ignore_selectors JSONB DEFAULT '[]'::jsonb,
    added_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    history_starts_at TIMESTAMPTZ,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    status TEXT NOT NULL DEFAULT 'active',
    last_checked_at TIMESTAMPTZ,
    is_test BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_pages_url ON pages (url);
CREATE INDEX IF NOT EXISTS idx_pages_active ON pages (active);
CREATE INDEX IF NOT EXISTS idx_pages_monitor_id ON pages (monitor_id);

CREATE TABLE IF NOT EXISTS versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    page_id UUID NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    version_no INT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    lag_seconds INT,
    content_hash TEXT NOT NULL,
    blocks JSONB NOT NULL,
    text_md TEXT,
    screenshot_url TEXT,
    screenshot_at TIMESTAMPTZ,
    source_url TEXT NOT NULL,
    run_id TEXT,
    classification JSONB,
    summary TEXT,
    UNIQUE(page_id, version_no)
);

CREATE INDEX IF NOT EXISTS idx_versions_page_version ON versions (page_id, version_no);
CREATE INDEX IF NOT EXISTS idx_versions_observed_at ON versions (page_id, observed_at);

CREATE TABLE IF NOT EXISTS diffs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    page_id UUID NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    from_version INT NOT NULL,
    to_version INT NOT NULL,
    diff JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_diffs_versions ON diffs (page_id, from_version, to_version);

CREATE TABLE IF NOT EXISTS checks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    page_id UUID NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    observed_at TIMESTAMPTZ NOT NULL,
    run_id TEXT UNIQUE,
    outcome TEXT NOT NULL, -- baseline | unchanged | noise | changed | error
    detail JSONB
);

CREATE INDEX IF NOT EXISTS idx_checks_page_observed ON checks (page_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_checks_run_id ON checks (run_id);

