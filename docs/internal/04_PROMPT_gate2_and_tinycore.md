# 04 — NEXT PROMPT FOR ANTIGRAVITY: GATE 2 PROBES → SPEC AMENDMENTS → TINYCORE BUILD

Paste everything below the line into Antigravity. Context: Gate 1 passed (see TINYFISH_NOTES.md). Gate 1 findings invalidate 3 assumptions in docs/00–03, so probe first, amend specs, then build.

---

## ROLE & RULES
Read AGENTS.md, DECISIONS.md, TINYFISH_NOTES.md, docs/00–03. Same hard rules (Python owns deterministic logic; Groq classify/extract only, validated; all TinyFish calls via `tinycore/tf_client.py`; no secrets in code). Decide and log in DECISIONS.md; don't ask questions. Do not start Part C until Part A passes or its fallback is chosen and logged.

## WHAT GATE 1 CHANGED (already known)
1. `GET /v1/monitors/{id}/runs` is 405 ⇒ **the "poll monitor events" design is invalid**. Events arrive via **webhook** or the response of `POST /v1/monitors/{id}/runs`.
2. **Fetch returns no screenshots** ⇒ screenshots must come from Agent steps ($0.016/step) or Browser API (CDP). Capture a screenshot **only** for baseline and for changes that survive the noise filter.
3. **Monitor cap = 50 active, shared across ALL three apps; min interval 30 min (topic monitors); $0.005 per completed run.** Allocation: Time Machine ≤30, TinyVerify ≤10, Site2MCP ≤2 (≤42 total, 8 spare).
4. `agent_config.max_steps` needs beta ⇒ omit it; enforce our own timeout + cost guard.
5. Fetch is free (150/min, 1000/day) ⇒ **use Fetch wherever possible; Agent only where interaction is required.**

## PART A — GATE 2 PROBES (target ≤1h) → write results into TINYFISH_NOTES.md under "## Gate 2"
Create `scripts/probe_gate2.py` (or one script per probe) and run each live.

**A1. Monitor type & change semantics (CRITICAL).**
- From docs/OpenAPI, list monitor types. Is there a type that watches a **specific URL for content change** (not only "topic" monitors)? What are its create params, and does min interval differ?
- Create a throwaway page we control (e.g. GitHub Pages or Vercel static file in this repo under `testpage/`). Create a monitor on it. Trigger `POST /v1/monitors/{id}/runs` for baseline. Edit the page, redeploy, trigger a run again.
- Record: the exact response/run payload for "no change" vs "changed"; does it include a changed flag, diff, old/new content, snapshot, hash? 
- **Pass:** we can reliably tell changed vs unchanged from a Monitor result. **Fail:** document; fallback = Monitor remains the trigger via webhook plus our content-hash verification via Fetch (log in DECISIONS.md).

**A2. Webhook receiver (always-on).** Free hosts sleep and would miss events.
- Build `infra/supabase/functions/tf-webhook/index.ts` (Supabase Edge Function, free, always-on) that validates any signature/secret header TinyFish provides, then inserts the raw JSON payload into table `monitor_events(id, received_at, monitor_id, run_id, payload jsonb, processed bool default false)`. Provide SQL migration `infra/supabase/schema.sql`. (If Edge Functions are impractical, use a Cloudflare Worker → Supabase REST insert; log choice.)
- Register as the monitor's webhook; trigger a run; confirm the row appears. Record webhook payload shape, headers, retry behavior, signature scheme.
- **Pass:** event row stored without any of our servers running.

**A3. Browser API screenshot.** 
- Call `POST https://api.browser.tinyfish.ai` for a CDP session; connect with Playwright `connect_over_cdp`; navigate to a pricing page; `page.screenshot(full_page=True)`.
- Record: latency, pricing/credit cost per session, max duration, whether full-page works. Compare cost vs Agent-step screenshot. Choose the cheaper reliable option as `tf.screenshot()`.
- Note: if Browser API does real work (screenshots/evidence), it counts as a **4th TinyFish endpoint** for the scoring tier. Record exact endpoint names used so we claim only what's real.

**A4. Credits & cost model.** Find balance/credit endpoint or dashboard figure; record free credits available. Compute and write estimated 14-day spend for: Time Machine (30 monitors; assume 22 pages @ every 6h + 8 high-churn @ every 2h ≈ 184 runs/day ≈ $0.92/day), baseline screenshots, change screenshots, TinyVerify demos (Agent steps × $0.016), Site2MCP tests. Put a table in TINYFISH_NOTES.md and flag if credits are insufficient.

**A5. Fetch-first registry lookup (accuracy + cost win).**
- Test `Fetch` on a deterministic search URL, e.g. `https://find-and-update.company-information.service.gov.uk/search/companies?q=deepmind` (format html) and on a company detail page. Parse rows/fields in Python (`selectolax`/`bs4`), no Agent.
- Compare to the Agent result (`DEEPMIND HOLDINGS LIMITED`, `12181850`). Record whether Fetch-only extraction is equal or better.
- Decision rule for TinyVerify adapters: **url_template + Fetch + Python parse first; Agent only when a form/JS interaction is required.**

**Gate 2 pass criteria:** A1 semantics known (or fallback logged), A2 webhook row stored, A3 screenshot method chosen, A4 budget written, A5 decision logged. Commit as `chore: gate 2 findings`.

## PART B — AMEND SPECS (edit docs in place, list changes in DECISIONS.md)
- `00_tinycore`: remove `monitor_events()` polling from tf_client; add `monitor_trigger_run(id)`, `register_webhook`; add `MonitorBudget` (hard cap 50, per-app quotas 30/10/2); add `CostLedger` (per-call cost, daily cap env `MAX_DAILY_USD`, abort + alert when exceeded); poller now **reads `monitor_events` table in Supabase** (processed=false), idempotent.
- `02_TimeMachine`: event source = webhook table; screenshot only for v1 and non-noise changes; interval default 360 min, ≥30 always; ≤30 pages. Keep rule: no own fetch-compare loop; Fetch is used only after a Monitor event to capture content.
- `01_TinyVerify`: adapters declare `search.mode: url_template|agent`; Fetch-first; Agent fallback; evidence screenshot via chosen screenshot method; monitor budget ≤10 watches (reject `/watch` beyond cap with a clear error).
- `03_SiteToMCP`: Monitor drift check uses ≤2 monitors; health check also runs on webhook event.

## PART C — BUILD `tinycore` (Must items first; commit after each)
1. `tf_client.py` using verified shapes: `X-API-Key`; `fetch(url, format)`, `agent_run(url, goal, schema)` (no max_steps; own timeout), `screenshot(url)` (method from A3), `monitor_create/trigger_run/delete`, `get_run`. Retries/backoff, typed errors, every call logged to `data/tf_calls.jsonl` **with cost estimate** and endpoint name.
2. `ledger.py` (CostLedger + MonitorBudget persisted in SQLite).
3. `normalize.py`, `differ.py` per spec + pytest fixtures (dates/ads/tracking noise must yield `is_empty`; price change must yield `changed` with `numeric_delta`).
4. `store.py` + `schema.sql` (+ Supabase Storage screenshot upload, local fallback).
5. `evidence.py`.
6. `events.py`: read/mark `monitor_events` from Supabase REST; idempotent processing.
7. `api_common.py`: `/health` (poller heartbeat, ledger, monitor count), `/endpoint-usage` (from tf_calls.jsonl, grouped by endpoint incl. cost).
8. `names.py` + tests (Should) · `llm.py` Groq classify with cache (Should).
Tests: `pytest -q` green; one `@live` test per TinyFish method.

## PART D — DELIVERABLE CHECK (print at end)
- Gate 2 table: probe → pass/fail → decision.
- Tree of repo; test results; `/endpoint-usage` sample JSON.
- Remaining TODOs for Time Machine MVP (next prompt).

## MoSCoW
- **Must:** A1, A2, A3, A4, tf_client, ledger, normalize, differ, store, events, api_common
- **Should:** A5 Fetch-first adapters, names.py, llm.py, retries telemetry
- **Could:** Telegram notifier, RSS
- **Won't:** captcha solving, relying on webhook-only on a sleeping host, simulated changes
