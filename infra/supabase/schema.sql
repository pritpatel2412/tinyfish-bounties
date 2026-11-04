-- infra/supabase/schema.sql
-- Table to store asynchronous webhook events from TinyFish

CREATE TABLE IF NOT EXISTS monitor_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    monitor_id TEXT,
    run_id TEXT,
    payload JSONB NOT NULL,
    processed BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_monitor_events_unprocessed 
    ON monitor_events (processed) 
    WHERE processed = FALSE;

CREATE INDEX IF NOT EXISTS idx_monitor_events_monitor_id 
    ON monitor_events (monitor_id);

CREATE INDEX IF NOT EXISTS idx_monitor_events_run_id 
    ON monitor_events (run_id);

ALTER TABLE monitor_events ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    DROP POLICY IF EXISTS "Allow all on monitor_events" ON monitor_events;
    CREATE POLICY "Allow all on monitor_events" ON monitor_events FOR ALL USING (true) WITH CHECK (true);
END $$;
