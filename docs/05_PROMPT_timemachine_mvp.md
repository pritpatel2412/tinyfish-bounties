# 05 — NEXT PROMPT FOR ANTIGRAVITY: TIME MACHINE MVP (BACKEND + DEPLOY, NO UI)

Paste everything below the line into Antigravity. Goal: **deployed and monitoring ≥20 vetted pages by Thu 6 Nov (target: Nov 4 night).** The 3-day live clock starts at first baseline.

---

## RULES
Read AGENTS.md, DECISIONS.md, TINYFISH_NOTES.md, docs/02_TimeMachine_SPEC.md. Same hard rules. Decide and log in DECISIONS.md; don't ask questions. Commit after every stage. Stop and report only if a Stage Gate fails.

## STAGE 0 — FIX GATE-2 GAPS FIRST (≤1h)
1. **Agent cost not recorded.** `/endpoint-usage` shows `agent_run` cost 0.0 despite a successful run. Compute `num_of_steps × 0.016` from the run response and record it in `CostLedger` + `tf_calls.jsonl`. Add a unit test with a mocked response.
2. **Webhook payload for `type: fetch` monitors is unverified.** The sample in TINYFISH_NOTES §3 is agent-shaped (`goal/result/steps`). Verify with a REAL scheduled run: create a temp fetch monitor on a page we control with `schedule_cron: "*/5 * * * *"` and `webhook_url` = the deployed Supabase function (`?secret=`). Wait for the scheduled delivery. Save the raw payload to `tests/fixtures/webhook_fetch_run.json`. Record in TINYFISH_NOTES: does the payload contain the page text (`results[].text`)? the run timestamp? Then delete the monitor.
   - If the payload has no text: processor fetches content via `GET /v1/runs/{run_id}` (X-API-Key) or Fetch. Log the choice.
3. **Edge Function actually deployed?** Confirm `tf-webhook` is deployed with JWT verification off (or token in query validated in code), the `monitor_events` table exists in the live Supabase project, and a real TinyFish webhook inserted a row. Print the row count.
4. **normalize.py must handle Markdown input.** Monitor and Fetch return `format: markdown`. Add `markdown_to_blocks(md)`: split on headings (`#`), table rows (`|`), list items, paragraphs; `heading_path` from nearest headings; keep numeric tokens intact. Tests: price change in a markdown table → `changed` with `numeric_delta`; reordered noise/dates/"last updated" → `is_empty`.
5. **Persistence.** Free hosts have ephemeral disks, so SQLite on the host will lose history. **Primary store for Time Machine = Supabase Postgres** (tables below). Implement `store.py` backend interface: `SqliteStore` (dev/tests) and `SupabaseStore` (prod, via `supabase-py` or REST). Select with `STORE_BACKEND`. Screenshots → Supabase Storage bucket `shots` (public read).

**Stage 0 gate:** tests green; fixture saved; deployed webhook row proven.

## STAGE 1 — SCHEMA (Supabase SQL in `infra/supabase/timemachine.sql`)
```
pages(id uuid pk, url text unique, title text, category text, monitor_id text, schedule_cron text, interval_label text,
      ignore_selectors jsonb default '[]', added_at timestamptz, history_starts_at timestamptz, active bool, status text, last_checked_at timestamptz)
versions(id uuid pk, page_id fk, version_no int, observed_at timestamptz, processed_at timestamptz, lag_seconds int,
         content_hash text, blocks jsonb, text_md text, screenshot_url text, screenshot_at timestamptz,
         source_url text, run_id text, classification jsonb, summary text, unique(page_id, version_no))
diffs(id uuid pk, page_id fk, from_version int, to_version int, diff jsonb, created_at)
checks(id uuid pk, page_id fk, observed_at timestamptz, run_id text unique, outcome text,  -- baseline|unchanged|noise|changed|error
       detail jsonb)
```
`observed_at` = TinyFish run time (fallback: webhook `received_at`). **As-of queries use `observed_at`, not processing time.** Every run writes a `checks` row (proves monitoring cadence; powers "last checked 12 min ago, no change").

## STAGE 2 — VET THE SEED LIST (`scripts/vet_pages.py` → `data/pages_vetted.json`)
Start from the 30 URLs in docs/02 (replace dead ones). For each URL:
1. Fetch markdown. Reject if: non-200, text < 800 chars, contains bot-wall markers ("Just a moment", "Verify you are human", "Access denied", captcha), or redirects to login.
2. **Stability test:** fetch twice 60s apart → `markdown_to_blocks` → `diff_blocks`. If diff not empty → noisy: auto-suggest `ignore` patterns, retry once; if still noisy → drop (log reason).
3. Keep ≥24 vetted pages; add replacements if needed (≥8 high-churn: changelogs, notices, release notes, gov news pages). Output per page: `url,title,category,churn:high|normal,schedule_cron`.
4. **Stagger crons** to avoid webhook bursts: normal pages `M */6 * * *`, high-churn `M */2 * * *`, where `M = hash(url) % 30` (UTC). Cap total ≤30 monitors, enforced against the **live** count from `GET /v1/monitors` (shared account), not only local state.
Print a table: url | status | stability | cron | reason-if-dropped. Commit `pages_vetted.json`.

## STAGE 3 — CAPTURE (`apps/timemachine/capture.py`)
`add_page(url, title, category, cron)`:
1. Live monitor-count check (≤30 for this app) → else typed error.
2. `tf.monitor_create(type="fetch", config={url, format:"markdown"}, schedule_cron, webhook_url=<EDGE_FN>?secret=...)`. The response's baseline `run.results[0].text` is **v1 content** (canonical content always comes from Monitor results).
3. `markdown_to_blocks` → store v1 (`observed_at` = baseline run time) + `checks(outcome=baseline)`.
4. `tf.screenshot(url)` (Browser CDP; cap full-page height, JPEG q≈70; ≤5 concurrent sessions via semaphore) → Storage → `versions.screenshot_url`, `screenshot_at`.
5. Set `history_starts_at = v1.observed_at`.
`scripts/seed.py`: add all vetted pages sequentially with 3s spacing; idempotent (skip existing URL); print summary + running monitor count + wallet balance.

## STAGE 4 — PROCESSOR (`apps/timemachine/processor.py`)
Loop every 60s (and on `/internal/wake`): read unprocessed `monitor_events`; per event, idempotent on `run_id`:
1. Map `monitor_id → page`. Unknown monitor → mark processed + log.
2. Errors in run → `checks(outcome=error)`, increment failure counter.
3. Extract text (payload, else `GET /v1/runs/{id}`). `markdown_to_blocks` (+ page `ignore_selectors`).
4. `diff_blocks(prev_version.blocks, new_blocks)`:
   - identical hash → `checks(unchanged)`; update `last_checked_at`.
   - `is_empty` → `checks(noise)` with reason; **no version**.
   - else → screenshot immediately (record `screenshot_at`; `lag_seconds = processed_at − observed_at`), new `versions` row, `diffs` row, then `llm.classify_change` (Groq; `price|limit|legal|feature|cosmetic`, materiality, 1-line summary; cache; if Groq fails store `classification=null`, never block).
5. Mark event processed. Wrap each event in try/except; one bad event never stops the loop.
**Rule:** if a diff is non-empty but classification is `cosmetic` the version is still stored (UI hides it). Python decides change/no-change; Groq only labels.

## STAGE 5 — API (`apps/timemachine/api.py`, FastAPI via `api_common`)
`POST /pages` · `GET /pages` (url, category, versions_count, last_checked_at, status) · `GET /pages/{id}/timeline` · `GET /pages/{id}/asof?date=` (latest version with `observed_at ≤ date`; before `history_starts_at` → 404 `BEFORE_HISTORY` with `history_starts_at`) · `GET /pages/{id}/diff?from=&to=` (dates or version_nos; compose structured diff between any two versions; `{added, removed, changed[{before,after,numeric_delta}]}`) · `GET /pages/{id}/versions/{n}` · `GET /changes?since=&category=&min_materiality=` · `GET /logs` (public: real checks+changes with timestamps, screenshot links, outcome counts per page) · `GET /export.json` · `GET /health` (processor heartbeat, last event age, unprocessed count, monitors active/paused, wallet balance, daily spend) · `GET /endpoint-usage` · `POST /internal/wake?secret=`.
Screenshot/source link and `observed_at` in every version payload. Pydantic response models; consistent error envelope.

## STAGE 6 — OPS & DEPLOY
1. **Watchdog** (hourly, in-process): `GET /v1/monitors` → any monitor `paused` (4 failures or empty wallet) → log, `PATCH` resume once, alert in `/health`; compare TinyFish monitor list to `pages` (drift). Wallet via `GET /v1/wallet`; if balance < $8 → `/health.status="LOW_FUNDS"`.
2. **Edge function** best-effort calls `POST {APP_URL}/internal/wake?secret=` after inserting an event (wakes sleeping host, cuts screenshot lag).
3. **Hosting:** Render free web service (Dockerfile + `render.yaml`): one process = FastAPI + background worker thread (processor + watchdog). Playwright connects to remote CDP, so no browser download in the image. Env vars from `.env.example`. **UptimeRobot** pings `/health` every 5 min (keeps it awake). Replit is the fallback if Render fails.
4. `MAX_DAILY_USD=2.50` enforced for all TinyFish calls; monitor schedule runs are billed by TinyFish regardless, so show projected daily burn in `/health`.
5. `scripts/daily_check.py`: prints pages count, checks last 24h, changes last 24h, unprocessed events, paused monitors, balance → run it each day.

## STAGE 7 — LIVE VERIFICATION (print at end)
- [ ] ≥20 pages seeded; monitors active count; each page has v1 + screenshot
- [ ] one temp test page with a deliberately edited mutable page (label it `test`, **exclude from public logs/claims**) proves: webhook → processor → new version → screenshot → diff → `/asof` + `/diff`
- [ ] `/health` green; UptimeRobot configured
- [ ] sample JSON for each endpoint saved to `samples/timemachine/`
- [ ] `daily_check.py` output
- [ ] Tests green: processor idempotency, noise vs change, asof boundaries, BEFORE_HISTORY

## MoSCoW
- **Must:** Stage 0 fixes, Supabase store, vetted ≥20 pages, capture+baseline+screenshots, processor, asof/diff/timeline/logs, deploy + keepalive, watchdog
- **Should:** Groq classification, per-page ignore selectors, `/export.json`, lag tracking
- **Could:** 1-hour interval for the 8 high-churn pages (+~$6.70 over 14 days; wallet allows), Agent fallback capture for JS-heavy pages **only if Fetch returns thin content and it's actually used**, RSS
- **Won't:** UI (separate Base44 prompt next), simulated changes in public logs, bot-walled pages
