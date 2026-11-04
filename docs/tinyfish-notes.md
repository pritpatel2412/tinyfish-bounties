# TinyFish Live Findings & Architecture Notes

Verified against live TinyFish API endpoints on **2026-11-03** using `scripts/smoke_tinyfish.py`.

---

## 1. Authentication & Base URLs

### Authentication
- **Header**: `X-API-Key: <TINYFISH_API_KEY>` required on all requests.
- Pass via HTTP header; direct bearer tokens are not standard.

### Base URLs
| Service | Base URL | Notes |
|---|---|---|
| **Fetch API** | `https://api.fetch.tinyfish.ai` | Rewrites internally to `/v1/fetch` |
| **Agent API** | `https://agent.tinyfish.ai` | Web automation, runs, and step screenshots |
| **Monitor API** | `https://agent.tinyfish.ai` | Page & Topic monitor CRUD and execution |
| **Browser API** | `https://api.browser.tinyfish.ai` | Direct CDP session provisioning |
| **Search API** | `https://api.search.tinyfish.ai` | Structured web search results |

---

## 2. Endpoint Paths & Methods

### Fetch API
- `POST https://api.fetch.tinyfish.ai`
  - Fetches and renders pages via headless Chromium.
  - Strips boilerplate, navigations, cookie banners, and advertisements.

### Agent API
- `POST https://agent.tinyfish.ai/v1/automation/run` — Synchronous agent execution (blocks until terminal state).
- `POST https://agent.tinyfish.ai/v1/automation/run-async` — Asynchronous start (returns `run_id` immediately).
- `POST https://agent.tinyfish.ai/v1/automation/run-sse` — Real-time Server-Sent Events stream.
- `GET https://agent.tinyfish.ai/v1/runs/{id}` — Retrieve run metadata, status, output, and execution steps.
  - Query param `?screenshots=url` (default): Step screenshot is an absolute URL.
  - Query param `?screenshots=base64`: Step screenshot is returned inline as `data:image/jpeg;base64,...`.
  - Query param `?screenshots=none`: Screenshots omitted from steps to save bandwidth.
- `GET https://agent.tinyfish.ai/v1/runs/{id}/steps/{step_id}/screenshot` — Returns raw binary `image/jpeg` bytes for a specific step (requires `X-API-Key`).

### Monitor API
- `POST https://agent.tinyfish.ai/v1/monitors` — Create monitor and automatically execute/return initial **baseline run**.
- `GET https://agent.tinyfish.ai/v1/monitors` — List all monitors for account (`{"monitors": [...]}`).
- `GET https://agent.tinyfish.ai/v1/monitors/{id}` — Get monitor configuration and status.
- `PATCH https://agent.tinyfish.ai/v1/monitors/{id}` — Pause, resume, or edit configuration.
- `DELETE https://agent.tinyfish.ai/v1/monitors/{id}` — Delete monitor and stop schedule (`{"deleted": true, "id": "..."}`).
- `POST https://agent.tinyfish.ai/v1/monitors/{id}/runs` — Trigger immediate manual run ("Run Now"), returns `{"run": {...}}`.
- *Note*: `GET /v1/monitors/{id}/runs` returns `405 Method Not Allowed`. Runs are returned upon creation, manual trigger, and posted to `webhook_url` on scheduled triggers.

---

## 3. Payload Shapes & Schemas

### Fetch API

#### Request Payload:
```json
{
  "urls": ["https://stripe.com/pricing"],
  "format": "markdown"
}
```
- Supported `format` values: `"markdown"` (default), `"html"`, `"json"`.
- Batch limit: up to 10 URLs per request.
- *Live finding*: Passing `"format": "screenshot"` triggers `HTTP 400 INVALID_INPUT: Field "format": Invalid option: expected one of "markdown"|"html"|"json"`. Fetch does not provide screenshots directly.

#### Response Payload (`200 OK`):
```json
{
  "results": [
    {
      "url": "https://stripe.com/pricing",
      "final_url": "https://stripe.com/pricing",
      "title": "Pricing & Fees",
      "description": "...",
      "language": "en",
      "format": "markdown",
      "text": "# Standard\n\nAccess a complete payments platform...",
      "author": null,
      "published_date": null,
      "latency_ms": 1321.85
    }
  ],
  "errors": []
}
```

---

### Agent API

#### Request Payload:
```json
{
  "url": "https://find-and-update.company-information.service.gov.uk/",
  "goal": "Search for company 'DeepMind' and extract the company name and company number of the first search result.",
  "output_schema": {
    "type": "object",
    "properties": {
      "company_name": {"type": "string"},
      "company_number": {"type": "string"}
    },
    "required": ["company_name", "company_number"]
  },
  "browser_profile": "lite"
}
```
- *Live finding*: Supplying `"agent_config": {"max_steps": 25}` returns `HTTP 403 FORBIDDEN: Your account is not enabled for custom max steps yet. Join the beta program at /beta, then retry.` Do NOT specify custom `max_steps` unless beta is enabled. The default agent step limit suffices.

#### Response Payload (`200 OK`):
```json
{
  "run_id": "5f1db515-14b8-410a-9110-d22d1c86630b",
  "status": "COMPLETED",
  "started_at": "2026-11-03T11:26:30.443Z",
  "finished_at": "2026-11-03T11:27:25.786Z",
  "num_of_steps": 10,
  "result": {
    "company_name": "DEEPMIND HOLDINGS LIMITED",
    "company_number": "12181850"
  },
  "error": null
}
```

---

### Monitor API

#### Create Request Payload:
```json
{
  "type": "fetch",
  "name": "Smoke Test Page Monitor",
  "config": {
    "url": "https://httpbin.org/html",
    "format": "markdown"
  },
  "schedule_cron": "0 * * * *",
  "purpose": "Detect changes in page text",
  "webhook_url": "https://example.com/webhooks/monitor"
}
```

#### Create Response Payload (`201 Created`):
```json
{
  "monitor": {
    "id": "f2ca5e91-3665-473a-a15f-478cf27864f0",
    "name": "Smoke Test Page Monitor",
    "type": "fetch",
    "config": {
      "url": "https://httpbin.org/html",
      "format": "markdown",
      "links": false,
      "image_links": false,
      "ttl": 0
    },
    "schedule_cron": "0 * * * *",
    "status": "active",
    "last_error": null,
    "webhook_url": "https://example.com/webhooks/monitor",
    "created_at": "2026-11-03 11:27:44",
    "updated_at": "2026-11-03 11:27:44"
  },
  "run": {
    "id": "20b591d2-18cd-4272-be33-aeaaac7779fb",
    "is_baseline": true,
    "results": [
      {
        "url": "https://httpbin.org/html",
        "final_url": "https://httpbin.org/html",
        "text": "...",
        "latency_ms": 312.3,
        "format": "markdown"
      }
    ],
    "errors": []
  }
}
```

#### Manual Run Response Payload (`POST /v1/monitors/{id}/runs` -> `201 Created`):
```json
{
  "run": {
    "id": "0075002b-73bd-4360-8d8c-a40c73ce5201",
    "is_baseline": false,
    "results": [
      {
        "url": "https://httpbin.org/html",
        "final_url": "https://httpbin.org/html",
        "text": "...",
        "latency_ms": 312.3,
        "format": "markdown"
      }
    ],
    "errors": []
  }
}
```

#### Webhook Notification Event Shape:
When a scheduled check detects a change (or finishes a run), TinyFish POSTs to `webhook_url`:
```json
{
  "event": "run.completed",
  "run_id": "0075002b-73bd-4360-8d8c-a40c73ce5201",
  "status": "COMPLETED",
  "data": {
    "run_id": "0075002b-73bd-4360-8d8c-a40c73ce5201",
    "status": "COMPLETED",
    "goal": "Detect changes in page text",
    "result": { ... },
    "steps": [ ... ]
  }
}
```

---

## 4. Monitor Min Interval, Quotas & Polling Strategy

- **Schedule Format**: Standard 5-field cron expression (`schedule_cron`). UTC default, or prefix `CRON_TZ=America/New_York`.
- **Minimum Interval**:
  - **Page Monitors (`fetch`)**: Standard cron frequencies (e.g. hourly `0 * * * *` or daily).
  - **Topic Monitors (`search`)**: Enforced minimum of **30 minutes** between checks.
- **Account Quotas**:
  - Max **50 active monitors** per account.
  - Auto-pause triggers: 4 consecutive failed checks or empty wallet balance.
- **Cost**: **$0.005 per completed check** (both baseline and manual runs are billed; failed runs are free).
- **Polling vs Webhook Architecture**:
  - Because `GET /v1/monitors/{id}/runs` is not a listing endpoint (405), TinyFish monitors rely primarily on **webhook callbacks** for scheduled notifications.
  - For serverless hosts that sleep (e.g., Replit free tier): webhooks can serve as an accelerator, while the application poller (`monitor_poller.py`) can trigger `POST /v1/monitors/{id}/runs` or compare normalized snapshots via Fetch.

---

## 5. Screenshot Method

1. **Fetch API**: Does NOT support screenshots directly (`format` only allows `markdown`, `html`, `json`).
2. **Agent API**: Every agent run captures step screenshots.
   - Run query: `GET https://agent.tinyfish.ai/v1/runs/{run_id}?screenshots=url` gives CDN URLs for steps.
   - Run query: `GET https://agent.tinyfish.ai/v1/runs/{run_id}?screenshots=base64` returns inline base64 JPEG data URIs.
   - Direct step binary: `GET https://agent.tinyfish.ai/v1/runs/{run_id}/steps/{step_id}/screenshot` with header `X-API-Key` returns raw binary `image/jpeg` (verified: 88,387 bytes).
3. **Arbitrary Page Screenshot Strategy**:
   - For `TF.screenshot(url)`: Execute an agent run with goal `"Navigate to the page and stay on it"` or use the direct Browser CDP API (`POST https://api.browser.tinyfish.ai` + Playwright `page.screenshot()`).

---

## 6. Credits, Rate Limits & Free Tier

| Product | Pricing | Free Allowance / Limits |
|---|---|---|
| **TinyFetch** | **Free** ($0.00 wallet cost) | 150 URLs / minute; 1,000 URLs / day |
| **TinySearch** | **Free** ($0.00 wallet cost) | 30 requests / minute; 500 requests / hour |
| **TinyAgent** | **$0.016 per step** | 2 concurrent runs on standard tier |
| **TinyBrowser** | **$0.002 per minute** | 5 concurrent sessions |
| **TinyMonitor** | **$0.005 per check** | 50 active monitors |
| **Wallet** | Pay-as-you-go | $8.00 free credit on new signup; $10 minimum deposit |

---

## 7. Implications for `tinycore/tf_client.py` & Pipeline

1. **`TF.fetch`**:
   - `render=True` is the default behavior of `https://api.fetch.tinyfish.ai` (Chromium rendering).
   - If `screenshot=True` is passed to `TF.fetch`: since the Fetch endpoint does not return screenshots, `TF.fetch` will fetch the page via Fetch API, and if a screenshot is explicitly requested, obtain it via an agent run or browser CDP session.
2. **`TF.agent_run`**:
   - Do NOT pass `agent_config.max_steps` unless the user account has custom max steps beta enabled (otherwise 403 error).
   - Use `browser_profile: "lite"` by default.
---

## Gate 2 Probes & Live Findings (2026-11-03)

### A1. Monitor Type & Change Semantics
- **Monitor Types**:
  1. **Page Monitor (`type: "fetch"`)**: Monitors a specific URL. Config accepts `url`, `format` (`markdown`|`html`|`json`). Schedule uses standard 5-field cron.
  2. **Topic Monitor (`type: "search"`)**: Executes search queries periodically (minimum interval: 30 minutes).
- **Change Semantics**:
  - Live probe on controlled mutable URL (`webhook.site`):
    - Baseline Run: returns `is_baseline: true` and full extracted markdown `results`.
    - Run 1 (No change): returns `is_baseline: false` and current markdown `results`.
    - Run 2 (Changed content $10->$15): returns `is_baseline: false` and updated markdown `results`.
  - **Critical Finding**: TinyFish run payloads do *not* contain an automated `changed: bool` or `diff` field. The API executes the fetch/search and returns the fresh content snapshot.
  - **Fallback / Architecture**: Monitor serves as the scheduled change trigger (webhook and `POST /runs`). Change detection, noise stripping, and diffing are performed deterministically in Python via `normalize.py` and `differ.py`.

### A2. Webhook Receiver (Always-on)
- **Delivery**: TinyFish POSTs to `webhook_url` upon terminal state (`run.completed`).
- **Headers**: Sent by Node runtime (`user-agent: node`, standard HTTP headers, no proprietary HMAC signature header).
- **Security / Validation**: Verified by passing a secret query token (e.g. `?secret=...`) or authenticating against TinyFish by querying `GET /v1/runs/{run_id}` with `X-API-Key`.
- **Infrastructure**:
  - `infra/supabase/schema.sql`: defines `monitor_events(id, received_at, monitor_id, run_id, payload jsonb, processed bool)`.
  - `infra/supabase/functions/tf-webhook/index.ts`: Supabase Edge function validating secret and inserting into `monitor_events`.

### A3. Browser API Screenshot (4th TinyFish Endpoint)
- **Endpoint**: `POST https://api.browser.tinyfish.ai` provisions a remote Chromium session and returns `{session_id, cdp_url, base_url}` in ~1.7s.
- **Playwright CDP**: Connected via `await chromium.connect_over_cdp(cdp_url)`.
- **Performance**: Captured 61 KB JPEG in ~9.5s.
- **Cost**: Metered at **$0.002 per minute**. A 12-second screenshot session costs **$0.0004 USD** (over 35x cheaper than an Agent step at $0.016).
- **Scoring**: Counting `api.browser.tinyfish.ai` brings TinyFish endpoint usage to 4 endpoints:
  1. `api.fetch.tinyfish.ai` (Fetch)
  2. `agent.tinyfish.ai/v1/automation` (Agent)
  3. `agent.tinyfish.ai/v1/monitors` (Monitor)
  4. `api.browser.tinyfish.ai` (Browser CDP)
- **Decision**: `tf.screenshot(url)` uses Browser API CDP session with fallback to Agent step.

### A4. Credits & Cost Model
- **Live Wallet Balance**: **$36.998533 USD** (`GET /v1/wallet`).
- **Rates**:
  - Fetch: $0.00 / URL (1,000 / day free)
  - Search: $0.00 / query (500 / hr free)
  - Monitor: $0.005 / completed run
  - Browser: $0.002 / minute (~$0.0004 / screenshot)
  - Agent: $0.016 / step
- **14-Day Budget Forecast**:
  | Item | Quantity / Frequency | Unit Cost | Total (14 Days) |
  |---|---|---|---|
  | **Time Machine Monitors** | 22 pages @ 6h + 8 pages @ 2h = 184 runs/day | $0.005 / run | $12.88 |
  | **Time Machine Screenshots** | 30 baseline + ~50 changes via Browser CDP | ~$0.0004 / shot | $0.03 |
  | **TinyVerify Monitors** | 10 watched records @ 12h = 20 runs/day | $0.005 / run | $1.40 |
  | **TinyVerify Agent Lookups**| ~30 fallback runs × ~5 steps = 150 steps | $0.016 / step | $2.40 |
  | **Site2MCP Monitors** | 2 listing drift monitors @ 6h = 8 runs/day | $0.005 / run | $0.56 |
  | **Site2MCP Agent Nav** | ~20 multi-step runs × 4 steps = 80 steps | $0.016 / step | $1.28 |
  | **Safety Buffer** | Unexpected reruns / live tests | — | $3.00 |
  | **Total Estimated Spend** | | | **~$21.55 USD** |

- **Verdict**: With $36.99 in wallet and ~$21.55 projected spend, credits are **100% sufficient** (+$15.44 surplus).

### A5. Fetch-First Registry Lookup
- **Test**: Search Companies House for "DeepMind".
- **Agent Search**: 55,342 ms, 10 steps, $0.160 USD cost.
- **Fetch Detail**: 14,453 ms, $0.000 USD cost.
- **Results Comparison**: Both returned exact company `DEEPMIND HOLDINGS LIMITED` (`12181850`).
- **Decision Rule**: `url_template + Fetch + Python parse first; Agent only when form/JS interaction is required.`




---

## 7. Stage 0 Live Verification Findings

- **Agent Cost Accounting**:
  - Live agent executions return `num_of_steps` in the JSON response payload.
  - Cost is calculated as `num_of_steps × $0.016 USD` and logged to both `CostLedger` (SQLite) and `data/tf_calls.jsonl`.
  - Added unit test in `tests/test_agent_cost.py` verifying full aggregation in `/endpoint-usage`.

- **Live `type: fetch` Monitor Run Payload**:
  - Verified with live runs (`POST /v1/monitors/{id}/runs` and scheduled monitor):
  - The run payload structure contains:
    - `id`: unique run ID (UUID).
    - `is_baseline`: boolean flag (`true` on initial creation, `false` on subsequent runs).
    - `results`: array of page results where `results[0].text` contains the complete rendered Markdown content.
    - `results[0].latency_ms`: page fetch latency.
  - Saved live payload fixture to `tests/fixtures/webhook_fetch_run.json`.
  - Content is consumed directly from `results[0].text`, completely eliminating extra GET or fetch calls during change processing.

- **Storage Architecture**:
  - `store.py` provides `SqliteStore` and `SupabaseStore` through `get_store()`.
  - `ScreenshotStore` targets Supabase Storage bucket `shots` (with local `/data/shots` fallback).
  - Migration schema defined in `infra/supabase/timemachine.sql`.

---

## 8. Time Machine Live Deployment & Seeding Metrics (2026-11-04)

- **Live Monitors on Account**: 34 active monitors (cap: 50).
- **Tracked Pages Seeded**: 25 pages across 6 categories:
  - 12 AI developer tools (Groq, Gemini, OpenAI, Replit, Supabase, Render, Cursor, Copilot, HuggingFace, Netlify, Resend)
  - 4 Changelogs & Release Notes (Vercel changelog, Anthropic release notes, Groq status, OpenAI status)
  - 4 Legal & Terms (OpenAI ToU, Google Privacy, GitHub Terms, Vercel Terms)
  - 3 Visas & Immigration (UK Student Visa, UK Skilled Worker Visa, USCIS Alerts)
  - 1 SaaS Pricing (Acme Cloud Platform Pricing live test page)
  - 1 Fintech Billing (Stripe Pricing)
- **Monitoring Checks in 24h**: 26 total checks logged in database:
  - 25 Baselines
  - 1 Real Material Change detected and classified
- **Wallet Balance**: **$36.36 USD** ($0.054 daily spend, $2.446 headroom under $2.50 daily ceiling).
- **Screenshots Captured**: 32 screenshots captured via Browser CDP API (`POST https://api.browser.tinyfish.ai` + Playwright) stored in `data/shots/`.
- **Judge Samples**: Generated and saved in `samples/timemachine/`:
  - `pages.json` (all tracked pages metadata and version counts)
  - `timeline.json` (descending chronological version history)
  - `asof_before.json` (point-in-time state before price increase: $29/mo)
  - `asof_after.json` (point-in-time state after price increase: $49/mo)
  - `diff_1_to_2.json` (structured diff with numeric delta: $29 -> $49/mo)
  - `changes.json` (global change feed with materiality: 0.9)
  - `logs.json` (public audit log of monitoring checks)
  - `export.json` (complete 2MB database dump for judges)


---

## 7. Stage 0 Live Verification Findings

- **Agent Cost Accounting**:
  - Live agent executions return `num_of_steps` in the JSON response payload.
  - Cost is calculated as `num_of_steps × $0.016 USD` and logged to both `CostLedger` (SQLite) and `data/tf_calls.jsonl`.
  - Added unit test in `tests/test_agent_cost.py` verifying full aggregation in `/endpoint-usage`.

- **Live `type: fetch` Monitor Run Payload**:
  - Verified with live runs (`POST /v1/monitors/{id}/runs` and scheduled monitor):
  - The run payload structure contains:
    - `id`: unique run ID (UUID).
    - `is_baseline`: boolean flag (`true` on initial creation, `false` on subsequent runs).
    - `results`: array of page results where `results[0].text` contains the complete rendered Markdown content.
    - `results[0].latency_ms`: page fetch latency.
  - Saved live payload fixture to `tests/fixtures/webhook_fetch_run.json`.
  - Content is consumed directly from `results[0].text`, completely eliminating extra GET or fetch calls during change processing.

- **Storage Architecture**:
  - `store.py` provides `SqliteStore` and `SupabaseStore` through `get_store()`.
  - `ScreenshotStore` targets Supabase Storage bucket `shots` (with local `/data/shots` fallback).
  - Migration schema defined in `infra/supabase/timemachine.sql`.


---

## 8. Scheduled Flow Cadence Verification (Step 6)

`scripts/verify_scheduled_flow.py` executed live verification against all 24 non-test pages on 2026-11-04:

```text
Loaded 24 tracked non-test pages from database.
Verification Timestamp: 2026-11-04T11:07:13.665521+00:00

=============================================================================================================================
Page URL / Title                           | Int   | Last Scheduled Check At     | Outcome    | Mins to Next / Overdue | Status
-----------------------------------------------------------------------------------------------------------------------------
Rate Limits - GroqDocs                     | 2h    | 2026-11-04T11:01:53.306833+00:00 | unchanged  | In 114.7 min           | [OK]
Rate limits | Gemini API | Google...       | 2h    | 2026-11-04T11:03:04.583969+00:00 | unchanged  | In 115.8 min           | [OK]
Business Pricing                           | 6h    | 2026-11-04T11:03:17.834557+00:00 | changed    | In 356.1 min           | [OK]
Pricing and Plans                          | 6h    | 2026-11-04T11:03:20.771715+00:00 | unchanged  | In 356.1 min           | [OK]
Pricing & Fees | Supabase                  | 6h    | 2026-11-04T11:03:26.017945+00:00 | unchanged  | In 356.2 min           | [OK]
Pricing                                    | 6h    | 2026-11-04T11:03:30.253350+00:00 | changed    | In 356.3 min           | [OK]
Cursor - Pricing                           | 6h    | 2026-11-04T11:03:34.053928+00:00 | unchanged  | In 356.3 min           | [OK]
GitHub Copilot - Plans & pricing           | 6h    | 2026-11-04T11:03:39.104005+00:00 | unchanged  | In 356.4 min           | [OK]
Pricing - Hugging Face                     | 6h    | 2026-11-04T11:03:43.001200+00:00 | unchanged  | In 356.5 min           | [OK]
Pricing and Plans | Netlify                | 6h    | 2026-11-04T11:03:46.536226+00:00 | unchanged  | In 356.5 min           | [OK]
Pricing & Fees (Stripe)                    | 6h    | 2026-11-04T11:03:51.428530+00:00 | changed    | In 356.6 min           | [OK]
Pricing - Resend                           | 6h    | 2026-11-04T11:03:57.337985+00:00 | unchanged  | In 356.7 min           | [OK]
Terms of Use (OpenAI)                      | 6h    | 2026-11-04T11:04:03.903847+00:00 | unchanged  | In 356.8 min           | [OK]
Privacy Policy - Google                    | 6h    | 2026-11-04T11:04:12.666991+00:00 | unchanged  | In 357.0 min           | [OK]
GitHub Terms of Service                    | 6h    | 2026-11-04T11:04:16.419277+00:00 | unchanged  | In 357.0 min           | [OK]
Terms of Service (Vercel)                  | 6h    | 2026-11-04T11:04:21.887906+00:00 | unchanged  | In 357.1 min           | [OK]
Student visa (Gov.uk)                      | 6h    | 2026-11-04T11:04:26.170147+00:00 | unchanged  | In 357.2 min           | [OK]
Skilled Worker visa (Gov.uk)               | 6h    | 2026-11-04T11:04:30.453355+00:00 | unchanged  | In 357.3 min           | [OK]
Changelog - Vercel                         | 2h    | 2026-11-04T11:04:35.115957+00:00 | changed    | In 117.4 min           | [OK]
Claude Platform release notes              | 2h    | 2026-11-04T11:04:42.320737+00:00 | unchanged  | In 117.5 min           | [OK]
USCIS Alerts                               | 2h    | 2026-11-04T11:04:45.666340+00:00 | unchanged  | In 117.5 min           | [OK]
OpenAI Status                              | 2h    | 2026-11-04T11:04:51.104033+00:00 | unchanged  | In 117.6 min           | [OK]
Groq Status                                | 2h    | 2026-11-04T11:04:55.361612+00:00 | unchanged  | In 117.7 min           | [OK]
GitHub Copilot licenses                    | 2h    | 2026-11-04T11:05:00.906410+00:00 | unchanged  | In 117.8 min           | [OK]
=============================================================================================================================

Summary: 24 total pages | 24 on cadence | 0 flagged
[OK] All pages are verified within scheduled monitoring cadence. Zero drift detected.
```
