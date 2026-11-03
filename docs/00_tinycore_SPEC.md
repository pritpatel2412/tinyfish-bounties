# 00 — TINYCORE (shared package) — AGENT EXECUTION SPEC

Target: Antigravity / Cursor. Language: Python 3.11. Deadline for all submissions: **Tue 17 Nov 2026**. Today: 3 Nov.
Monorepo: `tinyfish-bounties/` → `tinycore/` · `tinyverify/` · `timemachine/` · `site2mcp/`.

## HARD RULES
1. Python owns ALL deterministic logic (parsing, matching, status normalization, diffing, dedup, scheduling). LLM (Groq) only classifies/generates.
2. Every TinyFish call goes through `tinycore/tf_client.py`. No other file imports a TinyFish SDK/HTTP directly.
3. Every call is logged to `data/tf_calls.jsonl` `{ts, endpoint, url, ok, latency_ms, run_id}` → proof of endpoint usage for judges.
4. LLM output is never trusted for factual fields. Extraction fallback value must appear verbatim in the page text, else reject → `UNKNOWN`.
5. No secrets in code. `.env` only (`TINYFISH_API_KEY`, `GROQ_API_KEY`, `SUPABASE_URL`, `SUPABASE_KEY`). Provide `.env.example`.
6. Every result carries `source_url` + `checked_at` (UTC ISO-8601) + evidence hash.

## DAY-1 GATE (MUST, ~1–2h): TinyFish smoke test
Create `scripts/smoke_tinyfish.py`. Read TinyFish docs first. Write findings to `TINYFISH_NOTES.md`:
- Auth header + base URL; exact paths/params for **Agent**, **Fetch**, **Monitor** (create / list / get events / delete), screenshot support (which endpoint), JS rendering option.
- Monitor: min interval, max monitors on free tier, webhook vs poll, what an event payload contains (old/new content? hash? snapshot?).
- Credits/rate limits; per-call timeout.
- Test: (a) Fetch a pricing page, (b) Agent fill a search form on a public registry, (c) create a Monitor on a page and read its event, (d) obtain a screenshot.
**Do not write feature code until `TINYFISH_NOTES.md` is filled.** Anything marked `TODO(confirm)` below must be resolved from it.

## MODULES

### `tf_client.py`
```python
class TF:
    def fetch(self, url:str, render:bool=False, screenshot:bool=False) -> FetchResult   # html, text, status, screenshot_bytes|None, run_id
    def agent_run(self, url:str, goal:str, output_schema:dict, max_steps:int=25) -> AgentResult  # data(dict), run_id, trace_url|None
    def monitor_create(self, url:str, interval_minutes:int, label:str) -> str  # monitor_id
    def monitor_events(self, monitor_id:str, since:datetime|None) -> list[MonitorEvent]
    def monitor_delete(self, monitor_id:str) -> None
    def screenshot(self, url:str) -> bytes
```
- Retries: 3x exponential backoff on 429/5xx/timeouts. Typed errors: `TFRateLimited`, `TFBlocked`(captcha/403), `TFTimeout`, `TFSchemaMismatch`.
- `TODO(confirm)`: endpoint paths, auth, payload shapes from TINYFISH_NOTES.md.

### `normalize.py`
- `clean_html(html)->str`: drop script/style/noscript/nav/footer/cookie banners/ad iframes; keep headings, tables, lists.
- Noise strip (regex): ISO/US/EU dates & times, "Last updated…" lines, copyright years, UUIDs, nonces, CSRF tokens, `utm_*`/`gclid`/`fbclid` params, session ids, view counters.
- `to_blocks(text)->list[Block]`: split by headings/table rows/list items; `Block{id, heading_path, text, norm_hash}`.

### `differ.py`
- `diff_blocks(old:list[Block], new:list[Block]) -> Diff`
- `Diff{added:[Block], removed:[Block], changed:[{before,after,similarity,numeric_delta:[{old,new,unit}]}], is_empty:bool}`
- Matching: exact hash → then difflib ratio ≥ 0.6 within same `heading_path` = `changed`; else added/removed.
- Numeric extraction (₹ $ € £ %, `/mo`, `/yr`, `per 1M tokens`, request limits) → `numeric_delta`.
- `is_empty` after normalization ⇒ change is NOISE (store in noise_log only).

### `names.py` (TinyVerify)
- `normalize_name(s)`: NFKD, strip diacritics, casefold, drop honorifics (Dr, Mr, Mrs, Ms, Shri, Smt, Prof, CA, Adv), drop punctuation, collapse spaces.
- `score(a,b)`: max(rapidfuzz `token_sort_ratio`, Jaro-Winkler)/100, with initial-expansion (`P. Patel` ~ `Prit Patel`) and token-subset handling.
- `resolve(query, candidates)->Resolution`:
  - `MATCH` if top≥0.97 AND (top−second)≥0.05 (or identifier exact match)
  - `MULTIPLE` if ≥2 candidates ≥0.80 within 0.05 of top, or top in [0.80,0.97) → return ranked list, NEVER pick
  - `NO_MATCH` if top<0.80 or empty
- Optional identifier (licence no., reg no.) exact match always wins.

### `evidence.py`
`Evidence{source_url, checked_at, html_sha256, screenshot_url|None, tf_run_id, endpoint_used}`

### `store.py`
SQLite (`data/app.db`, WAL) + object storage abstraction: `ScreenshotStore.put(bytes,key)->url` (Supabase Storage free tier; fallback local `/data/shots`). Migrations in `schema.sql`.

### `llm.py` (Groq)
- Models: `TODO(confirm)` current free Groq model IDs (e.g. a Llama 3.x 70B for quality, 8B instant for cheap classify).
- `temperature=0`, JSON mode, disk cache keyed by `sha256(prompt)`, rate-limit aware (sleep on 429).
- Allowed functions ONLY: `classify_change(diff)->{type: price|limit|legal|feature|cosmetic, materiality:0-1, summary}` and `extract_fallback(text, schema)->dict` (validated by rule 4).

### `monitor_poller.py`
Cron loop (APScheduler) polling `tf.monitor_events` every 5–10 min for all registered monitors. **Do not rely on webhooks alone** (free hosts sleep). If webhook exists, use it as accelerator + poll as source of truth. Idempotent by `(monitor_id, event_id|content_hash)`.

### `api_common.py`
FastAPI app factory, `/health`, `/endpoint-usage` (aggregates `tf_calls.jsonl` by endpoint → shows Agent/Fetch/Monitor usage), CORS, JSON error envelope `{error:{code,message,source_url?}}`.

## FREE-STACK MAP
| Tool | Use |
|---|---|
| Antigravity | write/test all Python |
| Replit | host FastAPI + pollers (free tier may sleep → UptimeRobot ping every 5 min + poll design) |
| Base44 | dashboards only (prompts inside each spec) |
| Groq | classify/fallback extraction only |
| Supabase free | screenshots + optional Postgres |

## DEFINITION OF DONE (each submission)
- [ ] Public URL + GitHub repo + README (install, env, endpoints, 5-line TinyFish explanation: Agent / Fetch / Monitor roles)
- [ ] `/endpoint-usage` shows ≥3 TinyFish endpoints with real counts
- [ ] Demo video ≤3 min + sample JSON files committed
- [ ] Tests green (`pytest`), golden fixtures in `tests/fixtures/`

## MoSCoW (core)
- **Must:** smoke test, tf_client, normalize, differ, store, evidence, api_common, poller
- **Should:** names.py, llm.py classify, retry/telemetry
- **Could:** Telegram notifier (`notify.py`), RSS
- **Won't:** captcha solving, login-walled sites, any data behind ToS prohibitions

## HONEST RISKS
- TinyFish free credits/Monitor quota unknown → cap pages/registries to what quota allows; document the cap.
- 3 builds / 14 days alongside other work → protect order: Time Machine live (Nov 4–6) → TinyVerify → Site to MCP.