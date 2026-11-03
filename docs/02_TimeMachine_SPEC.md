# 02 — TIME MACHINE — AGENT EXECUTION SPEC
Depends on: `00_tinycore_SPEC.md`. **START FIRST.** Requirement: ≥3 days live with real caught changes before submitting.
**HARD DEADLINE: deployed and monitoring ≥20 pages by Thu 6 Nov** (earlier = more real changes). Submit Nov 10–16 with logs.

## PRODUCT
Version-history API over TinyFish Monitor: add any URL → baseline snapshot + screenshot → every real change creates a new version with screenshot + structured diff → query "what did it say on date X" and "what changed between X and Y".

## NON-NEGOTIABLE DESIGN RULE (Monitor must be core)
Change detection comes ONLY from TinyFish Monitor events. Our code never schedules its own fetch-compare loop. Our poller only reads Monitor events; on an event we use Fetch/screenshot to capture the content. Remove Monitor ⇒ nothing is ever detected.

## HONEST RISK
Pricing/ToS pages rarely change in 3 days. Mitigation: seed **≥10 high-churn pages** (docs, rate-limit pages, changelogs, government notice pages) so real changes are caught live. Never simulate changes. History is only claimed from first capture date (`history_starts_at`), never earlier.

## SEED PAGES (30; run `scripts/seed.py` which Fetch-checks each URL, drops non-200, reports count; URLs are my best guesses, replace dead ones)
**Dev/AI free-tier & pricing (students care):** groq.com/pricing · console.groq.com/docs/rate-limits · ai.google.dev/gemini-api/docs/pricing · ai.google.dev/gemini-api/docs/rate-limits · openai.com/api/pricing · replit.com/pricing · base44.com/pricing · vercel.com/pricing · supabase.com/pricing · render.com/pricing · cursor.com/pricing · github.com/features/copilot/plans · huggingface.co/pricing · netlify.com/pricing
**Terms/privacy:** openai.com/policies/terms-of-use · policies.google.com/privacy · docs.github.com/en/site-policy/github-terms/github-terms-of-service · vercel.com/legal/terms
**Visa/immigration (high value):** canada.ca study-permit page · gov.uk student visa page · gov.uk skilled worker visa page · immi.homeaffairs.gov.au student visa (500) page · travel.state.gov student visa page
**High-churn (guarantee real changes):** GitHub changelog · Vercel changelog · Groq changelog/status · IRCC news/notices page · USCIS alerts/news page · gov.uk visas news/updates page · a docs page that edits daily (e.g. Gemini API release notes)
Category column in DB: `saas_pricing | policy | visa | changelog`.

## DATA MODEL (SQLite, `schema.sql`)
```
pages(id, url UNIQUE, title, category, monitor_id, interval_minutes, added_at, history_starts_at, active)
versions(id, page_id, version_no, captured_at, content_hash, text_blocks_json, screenshot_url, source_url, monitor_event_id, change_summary_json NULL)
diffs(id, page_id, from_version, to_version, diff_json, classification_json, created_at)
noise_log(id, page_id, detected_at, monitor_event_id, reason)    -- events that normalized to empty diff
tf_calls (via tinycore)
```

## CAPTURE FLOW (`capture.py`)
1. `POST /pages {url, title?, category?, interval_minutes?}` → Fetch(render) → baseline `v1` (+screenshot) → `tf.monitor_create` → store `monitor_id`.
2. Poller (every 5–10 min): `tf.monitor_events(monitor_id, since=last)` → for each new event:
   a. `tf.fetch(url, render=True, screenshot=True)`
   b. `normalize.clean_html → to_blocks`
   c. `differ.diff_blocks(prev_blocks, new_blocks)`
   d. `is_empty` ⇒ write `noise_log`, **no new version**, stop.
   e. else: store screenshot, new `versions` row, `diffs` row; `llm.classify_change(diff)` → `classification_json` (`price|limit|legal|feature|cosmetic`, materiality, 1-line summary).
3. Idempotent on `monitor_event_id`.

## DIFF QUALITY (judged heavily)
- Noise rules (extend `normalize.py`): dates/timestamps, "last updated", copyright years, cookie/consent banners, ad slots, tracking params, A/B test ids, view counts, relative times ("2 hours ago").
- Per-page overrides: `pages.ignore_selectors` (JSON list) for stubborn dynamic regions.
- Output = structured `{added[], removed[], changed[{before,after,numeric_delta}]}`; UI hides `cosmetic` by default but keeps it in API.

## API
| Method | Path | Behavior |
|---|---|---|
| POST | `/pages` | add page (works for ANY URL) |
| GET | `/pages` | list + status (last_checked, versions_count) |
| GET | `/pages/{id}/timeline` | versions desc: `{version_no, captured_at, screenshot_url, source_url, summary, classification}` |
| GET | `/pages/{id}/asof?date=YYYY-MM-DD[THH:MM]` | version with max(captured_at) ≤ date: blocks + screenshot; if date < `history_starts_at` → 404 `{error:{code:"BEFORE_HISTORY", history_starts_at}}` |
| GET | `/pages/{id}/diff?from=&to=` | dates or version_nos; composes structured diff between any two versions |
| GET | `/pages/{id}/versions/{n}` | full version |
| GET | `/changes?since=&category=&min_materiality=` | global feed |
| GET | `/export.json` | everything (pages, versions meta, diffs) |
| GET | `/logs` | public change log (real events w/ timestamps) — shown to judges |
| GET | `/feed.xml` | RSS of material changes (Could) |
| GET | `/health`, `/endpoint-usage` | tinycore |

## UI (Base44 prompt — paste as-is)
> Build "Time Machine — a history of the web". Home: grid of tracked pages with category chips, last change time, version count, and a red dot when a material change occurred in the last 48h; "Add page" input (URL + category) calling POST {API_URL}/pages. Page detail: (1) a vertical timeline of versions, each with date, 1-line AI summary, classification chip (price/limit/legal/feature), thumbnail screenshot, and source link; (2) a date picker "What did this page say on…" calling /asof and showing the stored screenshot + text; (3) a two-date compare view calling /diff showing added (green), removed (red), changed (amber before→after) blocks and numeric deltas highlighted. Global "Changes" feed page from /changes with filters. Footer: link to /export.json and /logs. Clean editorial style, no gradients.

## README MUST INCLUDE
- Monitor role (sole change trigger), Fetch role (rendered content + screenshot at change time), Agent only if used (e.g. for pages needing interaction) — don't claim what isn't used.
- Live-run evidence: first-capture timestamp, count of real changes caught, link to `/logs`, screenshot examples.
- Page-add guide; noise-filter explanation with before/after example.

## MoSCoW
- **Must:** 20+ pages monitored via Monitor, baseline+change screenshots, as-of + diff API, timeline, `/logs`, ≥3 days uptime
- **Should:** noise filter + per-page overrides, Groq classify, Base44 UI, JSON export, 30 pages
- **Could:** RSS, Telegram alerts for `price|legal` changes, public "add a page" with rate limit
- **Won't:** login-walled/anti-bot pages, simulated changes presented as real

## BUILD ORDER
**Nov 3–4:** smoke test → `POST /pages` + baseline + monitor_create + poller + schema (MVP, no UI) → seed script → deploy on Replit + UptimeRobot.
**Nov 5–6:** confirm real events flowing, add remaining pages → differ/noise tuning.
**Nov 7–12:** UI, as-of/diff endpoints, classification, README, logs export.
**Nov 14–16:** capture final logs, record demo, submit.
**Checklist daily:** `/pages` count ≥20, `/logs` growing, poller heartbeat in `/health`.
