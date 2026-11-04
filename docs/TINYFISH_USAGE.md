# TinyFish API Usage, Latency & Economics

This document provides a detailed accounting of how we integrate with TinyFish's APIs, observed latency profiles, real-world economics, discovered quirks, and practical engineering guidance for fellow builders.

---

## 1. Endpoint Breakdown & Real Production Metrics

Data recorded live in `data/tf_calls.jsonl` across **269 calls** during testing, benchmarking, and fleet provisioning:

| Endpoint | Calls Logged | Success Rate | Avg Latency | Cost Model | Total Spend |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`fetch`** | 132 | 99.2% (131/132) | 4,191 ms | Free (\$0.00) (150/min, 1k/day) | **\$0.0000** |
| **`monitor_create`** | 44 | 88.6% (39/44) | 7,225 ms | \$0.005 / initial validation | **\$0.1850** |
| **`monitor_run`** | 32 | 100.0% (32/32) | 4,110 ms | \$0.005 / completed run | **\$0.1500** |
| **`monitor_delete`** | 18 | 100.0% (18/18) | 2,973 ms | Free (\$0.00) | **\$0.0000** |
| **`screenshot`** | 40 | 90.0% (36/40) | ~77 s | Included / Browser CDP | **\$0.0171** |
| **`agent_run`** | 2 | 50.0% (1/2) | 28,235 ms | \$0.016 / step | **\$0.0000** |
| **Total** | **269** | **95.5%** | — | — | **\$0.3521** |

---

## 2. Capabilities & Operational Roles

### 1. `POST /v1/monitors` (Monitor Management)
- **Primary Role**: Automated cron-based surveillance of target surfaces.
- **Schedule Syntax**: Standard 5-field cron syntax (e.g. `0 */6 * * *` for 6-hourly execution, `0 * * * *` for hourly).
- **Webhook Delivery**: Monitors push completed run notifications directly to our webhook endpoint (`https://.../webhook?secret=...`).

### 2. `POST /v1/fetch` (High-Speed Static Ingestion)
- **Primary Role**: Capturing rapid baseline content during onboarding and on-demand audits.
- **Output Format**: Returns pre-parsed `markdown` format, drastically simplifying HTML parsing while stripping non-semantic tags.
- **Economics**: 100% free under generous rate limits (150 requests/min, 1,000/day).

### 3. `POST /v1/runs` (Autonomous Agent Workflows)
- **Primary Role**: Complex multi-step interaction (e.g. filling out state registry search forms in TinyVerify).
- **Execution Model**: Dispatches autonomous browser sessions executing goals step-by-step.

### 4. Headless Chrome Browser CDP (Screenshot Archival)
- **Primary Role**: Capturing full-page visual evidence when a material change is confirmed.
- **Storage Strategy**: Captured JPEGs are streamed directly to Supabase Storage bucket `shots` and referenced as immutable public HTTPS URLs.

---

## 3. Discovered Quirks & Architectural Pivots (Gate 1 & Gate 2 Findings)

During initial prototyping and automated probing, we discovered several behavioral nuances that reshaped our system architecture:

### 1. `GET /v1/monitors/{id}/runs` Returns 405 Method Not Allowed
- **Initial Spec Assumption**: The initial system design assumed monitors could be periodically polled via `GET /runs`.
- **Finding**: TinyFish does not expose a polling endpoint for run listings.
- **Solution**: We pivoted entirely to a **push-based webhook architecture**. Webhooks deliver payloads immediately upon run completion, eliminating polling overhead and reducing latency to zero.

### 2. `POST /v1/fetch` Does Not Include Visual Screenshots
- **Initial Spec Assumption**: We hoped `fetch` would return an optional screenshot base64 blob.
- **Finding**: `fetch` is strictly a fast textual/markdown scraper and returns no visual assets.
- **Solution**: We separated text ingestion from visual evidence capture. Screenshots are captured exclusively for baseline snapshots and changes that survive our noise filter.

### 3. Monitor Minimum Intervals & Hard Quota
- **Finding**: The minimum execution interval for topic/content monitors is 30 minutes. The global active monitor quota is **50 active monitors** across an account.
- **Solution**: We established a rigorous fleet quota budget:
  - Time Machine: 28 active monitors (expanding up to 35).
  - TinyVerify: 10 reserved monitors.
  - Site to MCP: 2 reserved monitors.
  - Headroom: 5 spare monitors.

### 4. `agent_config.max_steps` Requires Beta Access
- **Finding**: Passing `max_steps` inside `agent_config` resulted in a beta access rejection.
- **Solution**: We omitted the parameter from outgoing payloads and enforced client-side timeouts and cost caps in `tf_client.py`.

---

## 4. Practical Advice for TinyFish Builders

1. **Use `Fetch` for Everything Static**: Because `fetch` is free and returns high-quality markdown, never use expensive agent runs for content that does not require interaction.
2. **Always Wrap in Exponential Backoff**: Monitor creation occasionally hits transient server timeouts during peak hours. Wrapping calls in `tenacity` or custom retry loops with jitter completely resolves sporadic failures.
3. **Audit Active Monitors Regularly**: Monitors continue running on schedule until explicitly deleted. Always implement an auditor script (see `scripts/audit_monitors.py`) to reconcile database state with live monitors and prevent budget leakage.
4. **Enforce Client-Side Cost Accounting**: Do not rely on monthly invoices. Track every call locally (see `tinycore/ledger.py`) to trigger proactive alerts before credit exhaustion.
