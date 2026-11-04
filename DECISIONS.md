# Architectural & Implementation Decisions

## 2026-11-03 — Day-1 Gate: TinyFish Smoke Test & API Discovery

### Decision 1: Dependency-Light Implementation for Smoke Testing
- **Context**: `scripts/smoke_tinyfish.py` needed to execute immediately without brittle external package dependencies or installation overhead.
- **Decision**: Implemented using Python standard library (`urllib.request`, `json`, `os`, `time`, `datetime`). Zero third-party dependencies required.
- **Consequences**: Fast, robust execution across any Python 3.11+ environment with native handling of HTTP errors, timeouts, and JSON payloads.

### Decision 2: Screenshot Handling Strategy
- **Context**: `docs/00_tinycore_SPEC.md` mentions `fetch(..., screenshot=True)` and `def screenshot(self, url:str) -> bytes`. However, live testing of `https://api.fetch.tinyfish.ai` proved that `format: "screenshot"` is invalid (HTTP 400), as Fetch only supports `"markdown"`, `"html"`, and `"json"`.
- **Decision**: Screenshots are captured via the TinyFish Agent execution steps (`GET https://agent.tinyfish.ai/v1/runs/{id}/steps/{step_id}/screenshot`) or direct Browser API CDP sessions. `scripts/smoke_tinyfish.py` extracts the step ID from the agent run details and downloads the raw `image/jpeg` binary evidence directly.

### Decision 3: Agent `agent_config` and `max_steps`
- **Context**: Passing `"agent_config": {"max_steps": 25}` triggered `HTTP 403 FORBIDDEN: Your account is not enabled for custom max steps yet. Join the beta program at /beta, then retry.`
- **Decision**: Omit `max_steps` from the request payload by default so TinyFish applies its standard defaults (~25–50 steps), avoiding account tier rejections while successfully completing form interactions.

### Decision 4: Audit Call Logging
- **Context**: Hard Rule 3 requires proof of endpoint usage logged to `data/tf_calls.jsonl` with `{ts, endpoint, url, ok, latency_ms, run_id}`.
- **Decision**: Implemented standardized atomic JSONL logging in `scripts/smoke_tinyfish.py` that will be shared with `tinycore/tf_client.py`.


## 2026-11-03 — Gate 2 Probes & Live API Findings

### Decision 5: Monitor Change Semantics & Fallback (Probe A1)
- **Finding**: Live testing of `POST /v1/monitors/{id}/runs` on a mutable URL with content changes demonstrated that TinyFish does not include an automated `changed: bool` or `diff` field in the run payload. It returns the current page snapshot.
- **Decision**: TinyFish Monitor acts as the scheduled trigger (webhook and `POST /runs`). Deterministic change detection is performed by our Python pipeline (`normalize.py` + `differ.py`), strictly adhering to Hard Rule 1.

### Decision 6: Always-on Webhook Storage (Probe A2)
- **Finding**: Background hosts sleep on free tiers and miss scheduled events. Webhooks sent by TinyFish contain `{event, run_id, status, data}`.
- **Decision**: Implemented Supabase Edge Function (`infra/supabase/functions/tf-webhook/index.ts`) and table `monitor_events` (`infra/supabase/schema.sql`). The application poller reads unprocessed rows from `monitor_events` via Supabase REST.

### Decision 7: Primary Screenshot via Browser API (Probe A3)
- **Finding**: Remote browser CDP sessions via `POST https://api.browser.tinyfish.ai` provision in ~1.7s and cost $0.002/min (~$0.0004 for a 12s screenshot session), compared to $0.016 for an Agent step.
- **Decision**: Implement `tf.screenshot(url)` using TinyFish Browser API CDP session with Playwright (animations disabled), falling back to Agent step if CDP fails. This also constitutes our 4th distinct TinyFish endpoint.

### Decision 8: 14-Day Budget Allocation & Hard Caps (Probe A4)
- **Finding**: Available wallet balance is $36.998533 USD.
- **Decision**: Set daily budget cap (`MAX_DAILY_USD=2.50`) and monitor quota allocations (Time Machine ≤30, TinyVerify ≤10, Site2MCP ≤2, total ≤42). Projected 14-day spend is ~$21.55 USD, ensuring zero risk of account depletion.

### Decision 9: Fetch-First Architecture for Registry Adapters (Probe A5)
- **Finding**: Fetching search and detail pages via `api.fetch.tinyfish.ai` took ~16s total at $0.00 cost and extracted exact company numbers and names, compared to 55s and $0.160 for Agent runs.
- **Decision**: TinyVerify adapters declare `search.mode: url_template | agent`. Adapters default to `url_template + Fetch + Python parse` first, reserving Agent strictly for sites with anti-bot, dynamic JavaScript form submission, or pagination requirements.


## 2026-11-03 — Stage 0: Time Machine MVP Backend Fixes

### Decision 10: Agent Cost Recording & Ledger Tracking
- **Finding**: Historical calls to `/endpoint-usage` reported `agent_run` cost as 0.0 because `num_of_steps` was not multiplied by $0.016 into `data/tf_calls.jsonl`.
- **Decision**: Enhanced `tinycore/tf_client.py` to extract `num_of_steps` (or step list length) and record `steps * 0.016` to both `CostLedger` and `data/tf_calls.jsonl`. Verified with comprehensive mocked unit tests in `tests/test_agent_cost.py`.

### Decision 11: Real `type: fetch` Monitor Run Payload Verification
- **Finding**: Executed live monitor run trigger against `https://httpbin.org/html`. The run payload directly provides `results[0].text` (full markdown), `is_baseline: bool`, `id: str` (run_id), and `latency_ms`.
- **Decision**: The Time Machine processor consumes `results[0].text` directly from the monitor event payload without needing an extra roundtrip fetch, saving latency and network calls.

### Decision 12: Dual Storage Backend (`SqliteStore` + `SupabaseStore`)
- **Finding**: Supabase credentials provided in `.env` contain the client-side publishable key (`sb_publishable_...`) without DDL administration privileges or edge function deployment CLI access.
- **Decision**: Implemented `store.py` with a pluggable backend interface: `SqliteStore` (fully persisted with WAL mode for dev, testing, and continuous operation) and `SupabaseStore` (for production Supabase PostgREST endpoints), selected via `STORE_BACKEND`. Schema migration written to `infra/supabase/timemachine.sql`.

## 2026-11-04 — Stage 1 to Stage 7: Time Machine Production Build & Live Verification

### Decision 13: Block Differ Matching by Positional Index
- **Finding**: During stability vetting, pages repeating identical text blocks under the same heading (e.g. repeated buttons like "Get started" or table cells) collided on block IDs derived from `f"{heading_path}:{norm_hash}"`. Differ matched only the first block, causing false-positive "removed" diffs.
- **Decision**: Updated `tinycore/differ.py` to match new blocks by their positional index `idx` in the `new` list rather than static hash IDs, completely eliminating false-positive diffs on Stripe, Netlify, Render, and Gov.uk.

### Decision 14: Strict Run ID Idempotency & O(1) Checks Query
- **Finding**: Scanning `get_checks(limit=500)` for run_id duplicate detection risked missing older checks on high-traffic pages and resulted in $O(N)$ overhead.
- **Decision**: Added `get_check_by_run_id(run_id)` with direct database index lookup (`checks(run_id)`) across both `SqliteStore` and `SupabaseStore`.

### Decision 15: Groq Model Selection (`qwen/qwen3.8-27b`)
- **Finding**: Legacy `llama-3.1-8b-instant` was deprecated on Groq API endpoint returning 404 `model_not_found`.
- **Decision**: Switched Groq default model in `tinycore/llm.py` to `qwen/qwen3.8-27b` which is active, ultra-fast (<800ms), and delivers exact JSON classifications.

### Decision 16: Live Mutable Test Page Architecture
- **Finding**: Proving live change detection requires a mutable remote HTTP endpoint that TinyFish monitors can access from the public internet.
- **Decision**: Utilized `webhook.site` with remote `PUT /token/{id}` content management. Created test page with initial pricing ($29/mo), captured baseline v1 + screenshot, mutated to $49/mo + upgraded features, triggered `POST /v1/monitors/{id}/runs`, and verified complete pipeline: processor detected change -> captured v2 screenshot via CDP -> computed structured diff -> Groq classified price increase -> stored Version 2 -> exported 8 judge samples.

### Decision 17: Stray Monitor Cleanup (Step 1)
- **Finding**: Running `scripts/audit_monitors.py` revealed 34 live monitors on TinyFish vs 25 tracked in DB (9 strays consisting of earlier testpage creations and aborted US visa monitor retries). Each stray monitor was incurring $0.005 per run.
- **Decision**: Deleted all 9 stray monitors from TinyFish. Live monitor count reduced from 34 to exactly 25, matching the tracked pages in database 1:1.

### Decision 18: Test Page Isolation & Real-Change Zero Baseline (Step 2)
- **Finding**: Counting controlled pipeline tests alongside real web changes would overclaim real caught changes to judges.
- **Decision**: Added `pages.is_test` column (default false). Marked controlled Acme test page `is_test = 1, active = 0, status = 'retired_test'`, and deleted its live monitor from TinyFish. Excluded `is_test` from `/pages`, `/changes`, `/logs`, and `/export.json` unless explicitly requested with `?include_test=1`. Moved pipeline verification samples into `samples/timemachine/verification/` with explanation README. Real change count verified at 0 for judge audit.

### Decision 19: Supabase Migration Script & Production Persistence Assertion (Step 3)
- **Finding**: Executing `scripts/migrate_sqlite_to_supabase.py` verified that the client publishable key in `.env` is limited to client operations and cannot execute DDL or create storage buckets under default RLS. The relational schema defined in `infra/supabase/timemachine.sql` and bucket `shots` must exist on the remote project before PostgREST accepts writes.
- **Decision**: Implemented `scripts/migrate_sqlite_to_supabase.py` with idempotent upsert logic across `pages`, `versions`, `diffs`, and `checks`, rewriting screenshot references to public HTTPS bucket URLs and reporting exact local vs remote row counts. Enforced a production startup assertion in `api.py` refusing to boot with ephemeral SQLite when `ENV=production` or `RENDER=true`. Fixed `SupabaseStore.update_version` and added `delete_page` to both storage backends.

### Decision 20: Public API Security & Access Guards (Step 4)
- **Finding**: Public endpoints on Render require strict isolation between public reads, authenticated mutations, and protected webhook receivers to prevent denial of service and quota hijacking.
- **Decision**: Enforced `X-Admin-Token` constant-time authentication (`secrets.compare_digest`) on `POST /pages`, `DELETE /pages/{id}`, and `POST /testpage/edit`. Enforced constant-time secret verification for `/webhook` (`WEBHOOK_SECRET`) and `/internal/wake` (`WAKE_SECRET`). Implemented standard library sliding-window rate limiting (60 GETs/min per client IP) and 5-minute response caching for `/export.json`. Restricted CORS to GET and preflight OPTIONS. Implemented `POST /suggest` for non-admin public page nominations. Audited entire git log verifying zero API secrets were ever committed.

### Decision 21: Render Cloud Readiness & Enhanced Health Telemetry (Step 5)
- **Finding**: Free-tier cloud hosts experience container restarts and sleep cycles, requiring dynamic port binding (`$PORT`), Singapore regional routing for low latency, cold-start event queue draining, and comprehensive operational health telemetry.
- **Decision**: Configured `Dockerfile` to dynamically bind to `${PORT:-8000}`. Updated `render.yaml` for `region: singapore` with all required production environment variables. Enhanced `apps/timemachine/api.py` and `tinycore/api_common.py` to drain pending events on container boot (`processor_worker.wake()`) and report heartbeat age, unprocessed events, active/paused monitor counts, wallet balance, daily spend, store backend, git sha, and uptime seconds on `/health`.

### Decision 22: Scheduled-Flow Verification & Cadence Proof (Step 6)
- **Finding**: Verifying continuous monitoring requires mathematical proof that every tracked target has an active check within 1.5× its schedule interval, with outcomes classified and logged.
- **Decision**: Implemented `scripts/verify_scheduled_flow.py` inspecting all 24 non-test pages in database. Supported `--trigger-due` to execute due monitor runs and drain events through the processing pipeline. Verified 24/24 pages are on cadence (0 flagged) with next checks scheduled in 115–358 minutes. Committed the live verification table to `TINYFISH_NOTES.md`.
