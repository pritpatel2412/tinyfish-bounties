# 03 — SITE TO MCP — AGENT EXECUTION SPEC
Depends on: `00_tinycore_SPEC.md`. Build window: Nov 10–13.
Bounty: turn a real website with no public API into an MCP server (≥3 typed tools), TinyFish doing the web work, health check that breaks loudly, demo in a real MCP client.
**Excluded by rules:** licence checks and property records.

## SITE SELECTION PROTOCOL (Must, first 1h)
1. Candidate A (preferred, high personal value): a **hackathon listing site with no public API** (e.g. Devfolio / Unstop / similar). Candidate B: a university or MOOC course catalogue (e.g. NPTEL course list). Candidate C: a city/club events or booking site.
2. For each: read `robots.txt` + ToS. **Drop any that forbid automated access.** Confirm no official public API exists.
3. Run 3 Agent/Fetch trials per candidate with `smoke_site.py`. Pick the first with clean structured results in 3/3 trials and stable listing + detail pages.
4. Record decision + reasoning in `SITE_DECISION.md`.

## TOOLS (adapt names to chosen site; example = hackathons)
All inputs/outputs are pydantic models → JSON Schema auto-exported to MCP.
| Tool | Input | Output |
|---|---|---|
| `search_listings` | `query?:str, mode?:"online"\|"offline"\|"any", deadline_before?:date, min_prize?:int, limit?:int=10` | `{items:[Listing], total_seen:int, source_url, checked_at}` |
| `get_listing_details` | `id:str` (or `url`) | `ListingDetail{title, organizer, deadline, dates, prize_text, prize_value_min, eligibility[], team_size{min,max}, tracks[], apply_url, source_url, checked_at}` |
| `list_closing_soon` | `days:int=7, limit:int=20` | `{items:[Listing], window_end}` |
| `get_eligibility_summary` | `id:str` | `{solo_allowed:bool|null, student_only:bool|null, regions[], raw_text, source_url}` — nulls when unknown, never guessed |
Add a 5th if natural. Multi-step use must work: search → details → eligibility.

## ARCHITECTURE (`site2mcp/`)
```
server.py            # FastMCP; stdio + streamable HTTP
models.py            # pydantic I/O
site_client.py       # ONLY place that calls tinycore.tf_client
parsers.py           # deterministic parsing (selectors/regex) → models
cache.py             # TTL cache (listings 10 min, details 60 min)
errors.py            # SiteChanged, SiteBlocked, NotFound, UpstreamTimeout → MCP isError with code
healthcheck.py       # golden queries + schema + fingerprint
fingerprint.py       # DOM skeleton hash of key pages
tests/
README.md
```
- **Fetch**: listing/detail pages (render if JS-heavy) → `parsers.py` extracts typed fields deterministically.
- **Agent**: only where interaction is required (applying filters, pagination, load-more, multi-step navigation) → `agent_run(..., output_schema)`; result validated against pydantic, else `SiteChanged`.
- **Groq**: only to normalize free-text (e.g. prize text → numeric min) AND validated; else `null`.
- Tool results never contain raw HTML dumps.

## HEALTH CHECK (loud failure) — judged
- `healthcheck.py` runs per tool a **golden query** with assertions (≥N results, required fields non-null, types valid, dates parse).
- **Fingerprint**: hash of tag/class skeleton for the listing + detail page; compare to stored baseline → `DRIFT` status.
- **TinyFish Monitor** on the listing page (3rd endpoint): event → run healthcheck immediately → write `health.json` + flip `/health` to `DEGRADED`.
- Tools check health flag: if `DEGRADED` and parse fails → raise `SITE_CHANGED` error with message "Site layout changed; tool X cannot parse field Y; last healthy at T". **Never return empty results silently.**
- `GET /health` (HTTP mode) + `python -m site2mcp.healthcheck` CLI exits non-zero on failure. Optional GitHub Actions cron (free).
- Include a **documented failure demo**: mutate a fixture HTML → show healthcheck fails with a clear message (fixture-based, labelled as such).

## RUN & INSTALL
- `pip install -e .` ; `python -m site2mcp` (stdio) ; `python -m site2mcp --http --port 8000`.
- Claude Desktop config in README:
```json
{"mcpServers":{"site2mcp":{"command":"python","args":["-m","site2mcp"],"env":{"TINYFISH_API_KEY":"...","GROQ_API_KEY":"..."}}}}
```
- Remote option: host HTTP mode on Replit → add as custom connector in Claude (if available on the plan; otherwise show stdio).

## DEMO (must be a real conversation, recorded)
Script (multi-step, uses ≥3 tools): 
1. "Which hackathons close in the next 7 days?" → `list_closing_soon`
2. "Only online ones with prize over ₹50k" → `search_listings`
3. "Can I enter solo as a student from India for the second one?" → `get_listing_details` + `get_eligibility_summary`
4. Show an error case: simulate layout change via fixture → agent reports `SITE_CHANGED` clearly.
Record screen with the MCP client visible (tool call panels shown).

## README OUTLINE
Why this site → tools table with schemas → install (3 commands) → Claude config → Agent/Fetch/Monitor usage (what each does, no overclaiming) → health check & failure modes → limits/ToS compliance → demo link.

## MoSCoW
- **Must:** site decision doc, 3 typed tools, Agent+Fetch real usage, healthcheck with golden queries, loud typed errors, real MCP-client demo, README
- **Should:** 4–5 tools, Monitor-triggered drift check (3rd endpoint), TTL cache, HTTP mode on Replit, tests
- **Could:** auto-generator CLI `site2mcp scaffold <url>` (Agent explores → drafts models/tools) — only if time remains; GitHub Actions health cron
- **Won't:** login-walled sites, licence/property data, scraping against ToS/robots

## BUILD ORDER (Nov 10–13)
D1 site decision + site_client + 1 tool end-to-end → D2 remaining tools + models + cache → D3 healthcheck + monitor drift + error codes → D4 demo, README, polish.
