# Time Machine REST API Reference

The Time Machine API allows clients and autonomous agents to audit web surface changes, reconstruct historical page state at arbitrary timestamps, stream semantic diffs, and inspect visual evidence.

---

## Base URLs
- **Local Development**: `http://127.0.0.1:8000`
- **Production (Render)**: `https://tinyfish-bounties.onrender.com`

---

## 🔒 Authentication & Headers

| Header | Required For | Format |
| :--- | :--- | :--- |
| `X-Admin-Token` | Admin endpoints (`POST /pages`, `DELETE /pages/{id}`, `/admin/health`) | Constant-time token comparison |
| `Content-Type` | Requests with JSON bodies | `application/json` |

Public query endpoints (`GET /health`, `GET /pages`, `GET /as-of`, `GET /changes`, `GET /export.json`, `POST /suggest`) require **no authentication**.

---

## 🚦 Error Handling & Envelope

All non-2xx responses return a structured JSON error envelope:

```json
{
  "error": {
    "code": "ERROR_CODE_STRING",
    "message": "Human-readable explanation of error",
    "path": "/requested-endpoint-path",
    "hint": "Optional actionable remedy"
  }
}
```

### Enumerated Error Codes

| Code | HTTP Status | Description |
| :--- | :--- | :--- |
| `PAGE_NOT_FOUND` | 404 | The requested URL or page ID is not registered in the monitoring fleet. |
| `BEFORE_HISTORY` | 400 | The requested timestamp `at` precedes the first recorded baseline (`history_starts_at`). |
| `INVALID_TIMESTAMP` | 400 | The provided `at` parameter could not be parsed as an ISO 8601 UTC timestamp. |
| `RATE_LIMITED` | 429 | Client exceeded the modest rate limit (60 requests/minute per IP). |
| `UNAUTHORIZED` | 401 | Missing or invalid `X-Admin-Token` header on protected endpoints. |
| `INVALID_WEBHOOK_SECRET` | 401 | Webhook delivery missing or provided mismatched `?secret=` query parameter. |

---

## 📡 Endpoints

### 1. Public Health Check
`GET /health`

Returns instantaneous operational status, uptime, and processor heartbeat lag.

#### Request
```bash
curl -X GET "https://tinyfish-bounties.onrender.com/health"
```

#### Response (200 OK)
```json
{
  "status": "healthy",
  "uptime_s": 3612.4,
  "processor_heartbeat_age_s": 8.5,
  "version": "1.0.0",
  "timestamp": "2026-11-04T18:50:00.000Z"
}
```

---

### 2. Admin Health & Telemetry
`GET /admin/health`

Exposes wallet balances, daily spend ledger, active monitor budgets, and degradation diagnostics. Requires `X-Admin-Token`.

#### Request
```bash
curl -X GET "https://tinyfish-bounties.onrender.com/admin/health" \
  -H "X-Admin-Token: your_admin_token"
```

#### Response (200 OK)
```json
{
  "status": "healthy",
  "version": "1.0.0",
  "timestamp": "2026-11-04T18:50:00.000Z",
  "uptime_s": 3612.4,
  "processor_heartbeat_age_s": 8.5,
  "unprocessed_events_count": 0,
  "monitors_active": 28,
  "monitors_paused": 0,
  "wallet_balance_usd": 35.82,
  "daily_spend_usd": 0.145,
  "store_backend": "supabase",
  "git_sha": "a4932fa0",
  "ledger": {
    "today_spend_usd": 0.145,
    "max_daily_usd": 1.5,
    "headroom_usd": 1.355
  },
  "monitors": {
    "global_cap": 50,
    "total_active": 28,
    "remaining_capacity": 22
  },
  "degraded_reasons": []
}
```

---

### 3. List Monitored Pages
`GET /pages`

Returns the catalogue of active monitored surfaces. Excludes test verification pages by default.

#### Query Parameters
- `category` *(optional, string)*: Filter by category (e.g. `framework_docs`, `saas_pricing`).
- `include_test` *(optional, integer, default: `0`)*: Set to `1` to include internal verification pages.

#### Request
```bash
curl -X GET "https://tinyfish-bounties.onrender.com/pages"
```

#### Response (200 OK)
```json
{
  "count": 28,
  "pages": [
    {
      "id": "e4a28292-0b1a-428a-86c0-7521e42a9881",
      "url": "https://fastapi.tiangolo.com/release-notes/",
      "title": "FastAPI Release Notes",
      "category": "framework_docs",
      "monitor_id": "mon_b1812fa",
      "schedule_cron": "0 */6 * * *",
      "interval_label": "every 6h",
      "added_at": "2026-11-04T00:00:00Z",
      "history_starts_at": "2026-11-04T00:00:00Z",
      "active": true,
      "status": "active"
    }
  ]
}
```

---

### 4. Temporal As-Of Query
`GET /as-of`

Reconstructs the precise state of any registered page at a specified historical timestamp. Returns the latest version observed on or before `at`.

#### Query Parameters
- `url` *(required, string)*: Exact URL of the monitored page.
- `at` *(required, string)*: ISO 8601 UTC timestamp (e.g. `2026-11-04T12:00:00Z`).

#### Request
```bash
curl -X GET "https://tinyfish-bounties.onrender.com/as-of?url=https://fastapi.tiangolo.com/release-notes/&at=2026-11-04T12:00:00Z"
```

#### Response (200 OK)
```json
{
  "page_id": "e4a28292-0b1a-428a-86c0-7521e42a9881",
  "url": "https://fastapi.tiangolo.com/release-notes/",
  "as_of_requested": "2026-11-04T12:00:00Z",
  "version_no": 1,
  "observed_at": "2026-11-04T00:00:00Z",
  "lag_seconds": 45,
  "content_hash": "b294e82f...",
  "blocks_count": 34,
  "screenshot_url": "https://xlljoenoqktpwqfvpidh.supabase.co/storage/v1/object/public/shots/e4a28292_v1.jpg",
  "source_url": "https://fastapi.tiangolo.com/release-notes/",
  "text_md": "# Release Notes\n\n## 0.115.0\n..."
}
```

#### Error Case: Timestamp Precedes History (400 Bad Request)
```json
{
  "error": {
    "code": "BEFORE_HISTORY",
    "message": "Timestamp 2026-10-01T00:00:00Z is before history begins for this page",
    "history_starts_at": "2026-11-04T00:00:00Z",
    "path": "/as-of"
  }
}
```

---

### 5. Stream Real-World Changes
`GET /changes`

Returns a list of verified substantive changes detected by the pipeline. Excludes noise rejections and internal test verification events.

#### Query Parameters
- `page_id` *(optional, string)*: Filter to a specific monitored page.
- `limit` *(optional, integer, default: `50`, max: `200`)*: Number of changes to return.
- `include_test` *(optional, integer, default: `0`)*: Include verification events.

#### Request
```bash
curl -X GET "https://tinyfish-bounties.onrender.com/changes?limit=20"
```

#### Response (200 OK)
```json
{
  "count": 0,
  "changes": []
}
```

---

### 6. Public Judge-Facing Export
`GET /export.json`

Provides a complete, self-contained JSON export of all monitored fleet metadata, check audit statistics, and change events. Cached in memory for 5 minutes.

#### Request
```bash
curl -X GET "https://tinyfish-bounties.onrender.com/export.json"
```

---

### 7. Suggest Page Candidate
`POST /suggest`

Public endpoint enabling external agents or community members to recommend new pages for monitoring.

#### Request
```bash
curl -X POST "https://tinyfish-bounties.onrender.com/suggest" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://docs.anthropic.com/en/release-notes/overview",
    "category": "ai_models"
  }'
```

#### Response (200 OK)
```json
{
  "status": "received",
  "message": "Thank you! Candidate queued for automated freshness & structure vetting."
}
```

---

### 8. Add Monitored Page (Admin)
`POST /pages`

Provisions a new page, establishes its initial baseline snapshot, and configures a TinyFish cron monitor. Requires `X-Admin-Token`.

#### Request
```bash
curl -X POST "https://tinyfish-bounties.onrender.com/pages" \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: your_admin_token" \
  -d '{
    "url": "https://resend.com/pricing",
    "title": "Resend Pricing",
    "category": "developer_tools",
    "schedule_cron": "0 */6 * * *"
  }'
```

#### Response (201 Created)
```json
{
  "status": "created",
  "page": {
    "id": "c1928374-9182-4912-bb32-901827481928",
    "url": "https://resend.com/pricing",
    "title": "Resend Pricing",
    "monitor_id": "mon_89123fe"
  }
}
```
