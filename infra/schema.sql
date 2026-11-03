-- infra/schema.sql
-- Local SQLite schema for tinycore, Time Machine, and TinyVerify

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- Budget and Allocations
CREATE TABLE IF NOT EXISTS monitor_allocations (
    app TEXT PRIMARY KEY,
    active_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS cost_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    date_str TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    cost REAL NOT NULL,
    details TEXT
);
CREATE INDEX IF NOT EXISTS idx_ledger_date ON cost_ledger(date_str);

-- Time Machine Pages and Versions
CREATE TABLE IF NOT EXISTS pages (
    id TEXT PRIMARY KEY,
    url TEXT UNIQUE NOT NULL,
    title TEXT,
    category TEXT DEFAULT 'saas_pricing',
    monitor_id TEXT,
    interval_minutes INTEGER DEFAULT 360,
    added_at TEXT NOT NULL,
    history_starts_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS versions (
    id TEXT PRIMARY KEY,
    page_id TEXT NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    version_no INTEGER NOT NULL,
    captured_at TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    text_blocks_json TEXT NOT NULL,
    screenshot_url TEXT,
    source_url TEXT NOT NULL,
    monitor_event_id TEXT,
    change_summary_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_versions_page_date ON versions(page_id, captured_at);

CREATE TABLE IF NOT EXISTS diffs (
    id TEXT PRIMARY KEY,
    page_id TEXT NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    from_version INTEGER NOT NULL,
    to_version INTEGER NOT NULL,
    diff_json TEXT NOT NULL,
    classification_json TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS noise_log (
    id TEXT PRIMARY KEY,
    page_id TEXT NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    detected_at TEXT NOT NULL,
    monitor_event_id TEXT,
    reason TEXT NOT NULL
);

-- Evidence Receipt Table
CREATE TABLE IF NOT EXISTS evidence_receipts (
    id TEXT PRIMARY KEY,
    source_url TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    html_sha256 TEXT NOT NULL,
    screenshot_url TEXT,
    tf_run_id TEXT,
    endpoint_used TEXT NOT NULL
);
