# tinycore — Shared Foundation

`tinycore` is the deterministic foundation powering all three web-intelligence services in this repository: **Time Machine**, **TinyVerify**, and **Site to MCP**.

In accordance with system design principles, all deterministic business logic (parsing, diffing, hashing, storage, rate limits, error budgets) is written in Python standard library and strict primitives. LLMs (via Groq) are only utilized for semantic classification and extraction.

---

## Architecture & Module Directory

```
tinycore/
├── tf_client.py       # Unified TinyFish API client (Fetch, Monitor, Agent, Browser, Wallet)
├── groq_client.py     # Schema-validated Groq LLM client (classification & entity extraction)
├── store.py           # Dual-backend storage layer (SqliteStore for dev, SupabaseStore for prod)
├── normalize.py       # Deterministic HTML/Markdown block normalizer & hash generator
├── differ.py          # Semantic block differ with numeric delta & keyword detection
├── noise.py           # Multi-tier noise filter (timestamps, counters, dynamic ads)
├── ledger.py          # Real-time cost ledger ($/day limits) and monitor quota manager
└── api_common.py      # Standardized FastAPI application factory, CORS, and health endpoints
```

---

## Module Details

### 1. `tf_client.py` — Unified TinyFish Client
- **Single Source of Truth**: All network calls to TinyFish services must route through `TF`. Direct HTTP calls to `api.tinyfish.io` outside this client are strictly forbidden.
- **Resilience**: Automatic retry on HTTP 429 (rate limits) and 5xx (server errors) with exponential backoff.
- **Transparency**: Every call logs timestamp, endpoint, latency, status, and estimated USD cost to `data/tf_calls.jsonl`.
- **Supported Endpoints**:
  - `fetch(url)`: Free high-speed static content retrieval (150/min, 1,000/day).
  - `monitor_create(url, schedule_cron, ...)`: Automated cron-scheduled monitoring ($0.005/completed run).
  - `monitor_run(monitor_id)`: On-demand manual trigger.
  - `agent_run(goal, url)`: Autonomous interactive browser workflows ($0.016/step).
  - `browser_screenshot(url)`: Headless browser screenshot capture via CDP.
  - `wallet_get()`: Real-time credit balance telemetry.

### 2. `groq_client.py` — Semantic LLM Interface
- **Strict Role**: LLMs are never used for parsing or deterministic logic. They classify diffs (e.g. `breaking_change`, `pricing_increase`, `security_patch`, `routine_update`) and extract structured entities.
- **Reliability**: Employs JSON mode with fallback validation and graceful degradation if the model API is unavailable.

### 3. `store.py` — Persistence Abstraction
- **Backend Swapping**: Configured via `STORE_BACKEND=sqlite` or `STORE_BACKEND=supabase`.
- **SqliteStore**: High-performance local development database with WAL mode and foreign key enforcement.
- **SupabaseStore**: Cloud PostgreSQL persistence over PostgREST with connection pooling.
- **ScreenshotStore**: Uploads captured visual evidence to the public Supabase Storage bucket `shots` with local fallback.

### 4. `normalize.py` & `differ.py` — Block Analysis
- **`normalize_html(html, ignore_selectors)` & `markdown_to_blocks(md)`**: Decomposes raw HTML/Markdown into a list of normalized `Block` items.
- **Heading Hierarchy**: Retains `heading_path` context (e.g., `["Pricing", "Pro Tier"]`) for every block.
- **Numeric Extraction**: Extracts and normalizes numeric values and currency symbols.
- **`diff_blocks(old_blocks, new_blocks)`**: Computes deterministic block additions, deletions, modifications, numeric changes, and percentage deltas.

### 5. `noise.py` — Multi-Tier Noise Reduction
Eliminates false alarms by filtering out:
- Date stamps, relative time phrases ("3 minutes ago", "Updated yesterday").
- Dynamic session IDs, view counters, CSRF tokens.
- Navigation menus, footers, cookie consent banners.

### 6. `ledger.py` — Cost & Quota Guard
- **Daily Budget Cap**: Enforces hard spending limits (e.g. max $1.50/day).
- **Global Monitor Cap**: Enforces the 50 active monitor limit across all apps.

### 7. `api_common.py` — FastAPI Standard
- **Uniform Error Envelope**:
  ```json
  {
    "error": {
      "code": "PAGE_NOT_FOUND",
      "message": "Page with ID ... not found",
      "path": "/pages/..."
    }
  }
  ```
- **Separation of Health Telemetry**:
  - Public `/health`: Unauthenticated status, uptime, processor heartbeat age, and version.
  - Admin `/admin/health`: Requires `X-Admin-Token`, exposing live wallet balance, daily spend, monitor quotas, and ledger metrics.
