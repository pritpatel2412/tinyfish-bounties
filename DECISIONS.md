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
