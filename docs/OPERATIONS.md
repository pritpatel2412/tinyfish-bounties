# Operations & Deployment Runbooks

This guide covers deployment procedures, production environment configurations, automated health alerting, and step-by-step incident response runbooks for Time Machine.

---

## 1. Cloud Infrastructure & Deployment

Time Machine is designed for high availability and low maintenance using Render and Supabase:
- **Compute**: Render Web Service (FastAPI / Uvicorn in Docker container, region `singapore`).
- **Database & Storage**: Supabase (PostgreSQL & S3-compatible Storage bucket `shots`).
- **Monitoring Trigger**: TinyFish Cloud monitors dispatching webhooks.

### Step-by-Step Deployment Guide

#### 1. Supabase Setup
1. Create a project in [Supabase](https://supabase.com).
2. Navigate to **SQL Editor** and paste the contents of [`infra/supabase/timemachine.sql`](../infra/supabase/timemachine.sql). Run the script to provision:
   - Tables: `pages`, `versions`, `diffs`, `checks`
   - Indexes: URL, monitor, timestamp indexes
   - Row Level Security (RLS) policies
   - Storage bucket `shots` configured for public read access.
3. Obtain your Project URL (`https://<project-id>.supabase.co`) and anon/service API keys from **Project Settings -> API**.

#### 2. Render Deployment
1. Connect your GitHub repository to [Render](https://render.com).
2. Choose **Blueprint** and select [`render.yaml`](../render.yaml) or create a **Web Service**:
   - **Environment**: Docker
   - **Region**: Singapore (`singapore`)
   - **Instance Type**: Free or Starter
   - **Health Check Path**: `/health`
3. Configure the Environment Variables listed in the table below.
4. Deploy the service. The service will automatically bind to `$PORT` and initialize workers.

---

## 2. Environment Variables Reference

| Variable | Required | Default | Purpose |
| :--- | :--- | :--- | :--- |
| `ENV` | Yes (in prod) | `development` | Setting to `production` enforces Supabase persistence and token security. |
| `STORE_BACKEND` | Yes | `sqlite` | Persistence engine: `sqlite` for local dev; `supabase` for cloud production. |
| `TINYFISH_API_KEY` | Yes | — | Secret key for TinyFish API (monitoring, fetch, agent, browser). |
| `GROQ_API_KEY` | Yes | — | API key for Groq Cloud (Llama 3 semantic change classification). |
| `SUPABASE_URL` | Yes (in prod) | — | Base URL of your remote Supabase project. |
| `SUPABASE_KEY` | Yes (in prod) | — | API key for Supabase PostgREST & Storage APIs. |
| `WEBHOOK_URL` | Yes (in prod) | — | Public URL where TinyFish sends webhook notifications. |
| `WEBHOOK_SECRET` | Recommended | — | Shared secret token passed in webhook query string (`?secret=...`). |
| `ADMIN_TOKEN` | Yes (in prod) | — | Secret token required for administrative mutations (`X-Admin-Token`). |
| `WAKE_SECRET` | Optional | — | Secret token for triggering asynchronous cold-start queue draining. |
| `PORT` | Auto-set | `8000` | Port assigned by Render or hosting environment. |

---

## 3. Monitoring, Telemetry & Alerting

### Health Endpoint Polling
- **Endpoint**: `GET /health`
- **Recommended Polling Interval**: Every 5 minutes via [UptimeRobot](https://uptimerobot.com) or Better Uptime.
- **Expected Response**: HTTP 200 with `status: "healthy"`.

### Degraded State Triggers
The service reports `status: "degraded"` under two operational conditions:
1. **Stale Processor Heartbeat**: Background processor has not completed a loop in $> 10$ minutes (`processor_heartbeat_age_s > 600`).
2. **Low Wallet Credit**: TinyFish wallet balance is below \$5.00 USD (`wallet_balance_usd < 5.0`).

---

## 4. Operational Runbooks

### Runbook A: Monitor Fleet Out of Sync
**Symptom**: Number of live TinyFish monitors does not match the count of pages in database.
1. Run the audit script:
   ```bash
   python scripts/audit_monitors.py
   ```
2. The script queries `GET /v1/monitors` from TinyFish and compares IDs against `pages.monitor_id`.
3. If orphaned monitors exist (e.g. from aborted tests), the script automatically deletes them to prevent credit burn.

### Runbook B: Daily Spend Spike
**Symptom**: `CostLedger` reports spend approaching the daily ceiling (\$1.50).
1. Inspect the admin health endpoint:
   ```bash
   curl -H "X-Admin-Token: $ADMIN_TOKEN" https://.../admin/health
   ```
2. Inspect `endpoint-usage`:
   ```bash
   curl https://.../endpoint-usage
   ```
3. If an automated script created unintended monitors or triggered excessive agent runs, pause monitors via database:
   ```sql
   UPDATE pages SET active = false WHERE active = true;
   ```

### Runbook C: Ephemeral Host Cold Start / Backlog Drain
**Symptom**: Container slept on Render free tier; several webhooks arrived while spinning up.
1. Render automatically boots container upon receiving HTTP request.
2. The lifespan handler executes `processor_worker.wake()` immediately on startup.
3. To manually trigger an immediate queue drain from external scripts:
   ```bash
   curl -X POST "https://.../wake?secret=$WAKE_SECRET"
   ```

### Runbook D: Migrating Local SQLite Database to Supabase
1. Ensure `infra/supabase/timemachine.sql` has been executed on the remote database.
2. Run the migration script:
   ```bash
   python scripts/migrate_sqlite_to_supabase.py
   ```
3. The script idempotently transfers all `pages`, `versions`, `diffs`, and `checks`, uploads local screenshots to the `shots` bucket, and verifies URL accessibility.
