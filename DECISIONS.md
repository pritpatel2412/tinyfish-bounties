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

### Decision 23: Secret Scan, Repository Restructure, Flagship Documentation & GitHub Launch (Prompt 07)
- **Finding**: Preparing the monorepo for public submission and judge evaluation required:
  1. Splitting `/health` to eliminate exposure of wallet balances and financial telemetry, reserving those to authenticated `/admin/health` (`X-Admin-Token`).
  2. Running exhaustive secret scanning on the entire commit history (`git log -p`) to guarantee zero exposed API keys or private credentials before publishing.
  3. Reorganizing the monorepo cleanly into `tinycore/` (shared foundation), `apps/timemachine/` (live MVP), `apps/tinyverify/` (in progress), `apps/site2mcp/` (in progress), and `docs/specs/`.
  4. Authoring exhaustive flagship documentation, sequence diagrams, before/after diff illustrations, and verified OpenAPI / curl specifications.
- **Decision**: Implemented `scripts/scan_secrets.py` and configured `.gitleaks.toml`. Reorganized repository layout and verified all 55 offline unit and integration tests remain 100% green. Authored root `README.md`, `apps/timemachine/README.md`, `docs/ARCHITECTURE.md`, `docs/TINYFISH_USAGE.md`, `docs/API_TIMEMACHINE.md`, and `docs/OPERATIONS.md`. Configured GitHub Actions CI workflow (`.github/workflows/ci.yml`). Renamed master branch to `main`, pushed to `https://github.com/pritpatel2412/tinyfish-bounties.git`, and tagged release `v0.1.0-timemachine`.


### Decision 24: Diagnosis of Scheduled Monitoring Flow & Missing Webhooks (Prompt 10 Part A)
- **Finding**: Running `scripts/diagnose_scheduled_flow.py` against live TinyFish and Supabase endpoints revealed:
  1. 24 of 28 TinyFish monitors had an `<EMPTY>` `webhook_url`, and 4 pointed to a development `smee.io` relay.
  2. The `monitor_events` table did not exist in Supabase (`HTTP 404`).
  3. The Supabase Edge Function `tf-webhook` was not deployed (`HTTP 404`).
  4. While ~114 scheduled runs were expected and billed on TinyFish cloud (~$1.24 wallet reduction across ~247 operations), zero events were received on the server because the webhook destination was unreachable or unconfigured.
- **Decision**: Documented all diagnostic findings. Prepared `infra/supabase/schema.sql` (with `monitor_events` and RLS policies) and updated `infra/supabase/functions/tf-webhook/index.ts` with asynchronous Render wake notification. Halted mutations until user completes mandatory Supabase and Render dashboard setup.


### Decision 25: Delivery Fix, Natural-Delivery Proof & Data Integrity Hardening (Prompt 10 Parts B & C)
- **Finding**:
  1. `PATCH /v1/monitors/{id}` rejects `webhook_url` (400 Bad Request). Changing webhook destinations requires recreating monitors.
  2. Natural scheduled delivery needed concrete end-to-end proof without manual `/runs` intervention.
  3. Provenance of checks required strict tracking (`baseline|scheduled|manual`) to prevent self-fulfilling cadence claims.
  4. Flapping and capture variance required a 3-observation confirmation gate before promoting changes to new versions.
  5. All screenshots needed public HTTPS URLs on Supabase Storage bucket `shots` rather than ephemeral local disk paths.
- **Decision**:
  1. **Delivery Fix (Part B)**: Migrated all 28 monitors to Supabase Edge Function `tf-webhook` with `WEBHOOK_SECRET`. Proved natural scheduled delivery via `scripts/run_b3_natural_proof.py` at `2026-11-04T16:00:05.780265 UTC` on cron `*/5 * * * *` without calling `/runs`.
  2. **Trigger Provenance (C1 & C2)**: Added `checks.trigger` (`baseline|scheduled|manual`). Backfilled 24 manual checks from 11:01–11:05 UTC and 29 baseline checks across SQLite and Supabase. Exposed public clock metrics on `/health` and `/logs`.
  3. **Confirmation Gate & Materiality (C3 & C4)**: Implemented `confirm_change` (2 immediate fetches >=90s apart requiring >=2 of 3 observations to agree). Enforced daily 1,000 fetch budget in `CostLedger`. Python computes deterministic materiality and `suspect_capture_variance`; Groq fallback chain (`GROQ_MODELS`) handles `{type, summary}` without defaulting to "price".
  4. **Human Review Gate (C5 & C6)**: Added `changes.review_status` (`unreviewed|real|artifact`) and `POST /admin/changes/{id}/review` (X-Admin-Token). Default `/changes` displays verified real changes only. Compiled full review pack for existing manual changes in `docs/REVIEW_2026-11-04.md`.
  5. **Screenshots Hardening (C7)**: Captured missing baseline screenshots for `status.groq.com`, `vercel.com/changelog`, `vercel.com/legal/terms`, and `openai.com/policies/terms-of-use`. Uploaded all 47 screenshots to Supabase Storage bucket `shots`, rewritten URLs to public HTTPS, and verified anonymous downloads return HTTP 200 `image/jpeg`. Enforced production rejection of non-https screenshot URLs.


### Decision 26: Backend Prerequisites & Contract Alignment for Base44 UI (Prompt 11)
- **Finding**: Connecting the production Base44 UI required:
  1. `GET /stats`: Unified operational metrics (`pages_tracked`, `scheduled_checks_total`, `manual_checks_total`, `first_scheduled_check_at`, `days_of_scheduled_history`, `confirmed_changes`, `unreviewed_changes`, `outcome_counts:{baseline,unchanged,noise,unconfirmed,changed,error}`) strictly excluding test pages.
  2. `/pages`: Comprehensive metadata contract (`id, url, title, category, versions_count, last_checked_at, status, history_starts_at, interval_label`).
  3. `/changes`: Parameter filtering (`category, min_materiality, include=all|real`), diff stats (`+a -r ~c`), and strictly https screenshot URLs.
  4. `/logs`: Explicit trigger provenance (`baseline|scheduled|manual`).
  5. Route-aware CORS: Permitting any client origin (including Base44 app domains) for all GET endpoints, while permitting POST + OPTIONS exclusively on `/suggest`.
  6. `/suggest`: Sliding-window rate limiter (5/hr/IP), strict HTTP(S) URL validation, saving nominations to `suggestions` table without monitor creation.
- **Decision**: Added `GET /stats` endpoint, aligned `/pages` schema with `versions_count`, upgraded `PageSuggestRequest` to accept `note` with 5/hr rate limiting, implemented route-aware CORS middleware, and added `suggestions` table in SQLite/Supabase. Verified 100% test pass (62 tests).

### Decision 16 — CI Offline Test Suite Environment Isolation
- **Context**: GitHub Actions CI workflow runs without a `.env` file and failed in the "Run Offline Test Suite" step with `ValueError: TINYFISH_API_KEY not configured in environment or .env`.
- **Root Cause**: Components previously relied on direct `.env` parsing rather than falling back to `os.getenv` or test mock tokens when executing in test mode (`ENV=test` / `PYTEST_CURRENT_TEST`).
- **Fix**:
  1. Updated `tinycore/tf_client.py` and Time Machine modules (`processor.py`, `capture.py`, `watchdog.py`) to check `os.getenv("TINYFISH_API_KEY")` with fallback to `mock-test-key` in test environments.
  2. Configured test defaults in `tests/conftest.py` and `.github/workflows/ci.yml` providing mock keys (`TINYFISH_API_KEY: mock-test-key`, `GROQ_API_KEY: mock-test-key`).
  3. Added `evidence_receipts` DDL into `tinycore/store.py` (`init_db`) so fresh runner databases create all tables required by `test_store_evidence.py`.
  4. Verified offline suite (62/62 tests passing without `.env` and with fresh DB) and zero ruff violations.

### Decision 17 — MonitorEvent Object vs Mapping Compatibility in Production Event Processor
- **Context**: In production on Render (`STORE_BACKEND=supabase`), the application crashed on startup during `processor_worker.wake()` with `AttributeError: 'MonitorEvent' object has no attribute 'get'`.
- **Root Cause**: `tinycore/events.py` returns `list[MonitorEvent]` dataclass instances, whereas `apps/timemachine/processor.py` expected dictionary mappings (`event.get(...)`, `ev.get('id')`). The unhandled exception in `processor_worker.wake()` caused FastAPI lifespan startup to abort and exit.
- **Fix**:
  1. Extended `MonitorEvent` dataclass in `tinycore/events.py` with `.get(key, default=None)`, `.to_dict()`, and `__getitem__` for transparent duck-typing as a mapping.
  2. In `apps/timemachine/processor.py`:
     - `get_unprocessed_events()` normalizes Supabase events to dictionaries via `to_dict()`.
     - `process_single_event()` supports both dictionary objects and dataclasses.
     - `process_pending_events()` safely extracts event IDs during error handling.
     - `processor_worker.wake()` wraps the cold-start drain in a try-except block so background processing errors cannot abort FastAPI application startup.
  3. Added unit tests in `tests/unit/test_data_integrity.py` verifying full `MonitorEvent` object and dict compatibility. All 63 tests passing.

### Decision 18 — Production Supabase Store Resiliency, Concurrency Locking, and Screenshot Loop Isolation
- **Context**: Production logs on Render reported:
  1. Unique constraint 409 collisions on `checks_run_id_key` and `versions_page_id_version_no_key`.
  2. `AttributeError: 'SupabaseStore' object has no attribute '_conn'` during version updates.
  3. `RuntimeError: asyncio.run() cannot be called from a running event loop` during screenshots.
  4. Groq 400 `context_length_exceeded` on large page diffs.
  5. Render startup port scan delays caused by synchronous cold-start wake blocking lifespan yield.
- **Root Cause & Fixes**:
  1. `SupabaseStore.update_version`: Replaced residual SQLite `self._conn()` call with Supabase REST `PATCH`.
  2. Idempotent 409 handling: `SupabaseStore.add_check` and `add_version` catch `23505` duplicate key errors and return existing records instead of aborting.
  3. Screenshot event loop isolation: Wrapped `asyncio.run(_grab_shot())` with `concurrent.futures.ThreadPoolExecutor` when an asyncio event loop is already active on the caller thread.
  4. Groq context compaction: Sliced samples (10 items per category) and capped diff JSON at 8,000 characters before LLM classification.
  5. ProcessorWorker concurrency & non-blocking wake: Introduced `_process_lock` to eliminate concurrent duplicate event processing, and `_wake_event` to trigger background worker execution without blocking FastAPI startup or delaying Render port binding.
