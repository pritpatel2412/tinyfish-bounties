# 01 — TINYVERIFY — AGENT EXECUTION SPEC
Depends on: `00_tinycore_SPEC.md` (tinycore must exist + TINYFISH_NOTES.md filled).
Bounty: verify a person/business licence or registration live from official registries (≥2), return structured record, Monitor flags status changes.

## PRODUCT IN ONE LINE
`POST /verify {name, record_type, jurisdiction}` → live, evidence-backed record or an explicit `NO_MATCH` / `MULTIPLE`; `POST /watch` → Monitor alerts on status change.

## DIFFERENTIATORS (build these deliberately)
1. **Config-driven registry adapters (YAML)** — adding a registry = adding a file, zero code. Drives Coverage score.
2. **Ambiguity engine** in Python (`tinycore.names.resolve`) — never guesses.
3. **Evidence receipt** per result: URL + UTC timestamp + html_sha256 + screenshot.
4. **Cross-registry corroboration**: if 2 registries cover same record_type, compare → `conflict` flag.
5. **Honest `UNSUPPORTED`/`BLOCKED`** for captcha/login registries instead of fabricating.

## API
| Method | Path | Notes |
|---|---|---|
| POST | `/verify` | body `{name, record_type, jurisdiction, identifier?, entity_type?}` |
| POST | `/verify/select` | body `{query_id, candidate_id}` → resolves a `MULTIPLE` outcome |
| POST | `/watch` | `{record_id, interval_minutes?=360, notify_url?}` → creates TinyFish Monitor |
| DELETE | `/watch/{id}` | |
| GET | `/records/{id}` | latest + history |
| GET | `/changes?since=` | status-change feed (JSON) |
| GET | `/registries` | adapters + supported (country, record_type), health |
| GET | `/health`, `/endpoint-usage` | from api_common |

## RESULT SCHEMA (pydantic `VerifyResponse`)
```json
{
  "query_id": "uuid",
  "outcome": "MATCH | MULTIPLE | NO_MATCH | UNSUPPORTED | BLOCKED",
  "query": {"name":"", "record_type":"", "jurisdiction":""},
  "records": [{
    "record_id": "uuid",
    "registry_id": "uk_companies_house",
    "subject_name": "", "name_variants_seen": [],
    "record_type": "company_registration",
    "status": "ACTIVE | EXPIRED | SUSPENDED | REVOKED | INACTIVE | UNKNOWN",
    "raw_status": "as printed by registry",
    "licence_number": "", "issue_date": null, "expiry_date": null,
    "disciplinary_actions": [{"date":"","description":"","source_url":""}],
    "match_confidence": 0.0,
    "evidence": {"source_url":"","checked_at":"","html_sha256":"","screenshot_url":"","tf_run_id":"","endpoints":["agent","fetch"]}
  }],
  "candidates": [{"candidate_id":"","display_name":"","location":"","identifier":"","score":0.0,"detail_url":""}],
  "corroboration": {"registries_checked":2,"agree":true,"conflicts":[]},
  "notes": ["why NO_MATCH / what was not checked"]
}
```
`MULTIPLE` ⇒ `records=[]`, `candidates` ranked. `NO_MATCH` ⇒ say which registries were searched + name variants tried.

## ADAPTER SPEC (`registries/*.yaml`)
```yaml
id: uk_companies_house
country: GB
record_types: [company_registration]
entity_type: business            # person | business
base_url: https://find-and-update.company-information.service.gov.uk/
search:
  url: https://find-and-update.company-information.service.gov.uk/
  agent_goal: "Search the company name '{name}', return up to 10 result rows with name, number, status, address, detail link."
  result_schema: {rows: [{name: str, identifier: str, status_text: str, location: str, detail_url: str}]}
detail:
  fetch_url_template: "{detail_url}"
  selectors:                      # deterministic first
    status: "css:..."             # fill from real page during dev
    licence_number: "regex:Company number\\s*(\\w+)"
    issue_date: "regex:Incorporated on\\s*([\\d\\w ]+)"
  fallback: groq_extract           # validated verbatim
status_map: {active: ACTIVE, dissolved: INACTIVE, liquidation: SUSPENDED, "in administration": SUSPENDED}
captcha: false
tos_notes: "public search, no login; respect rate limit 1 req/3s"
verified_on: null                  # set by smoke script
```
Registry candidates (**verify each live with the smoke script; keep only those that work; URLs are unverified by me**):
- **GB**: Companies House search (business), FCA Register (firms/individuals, disciplinary info).
- **IN**: NMC Indian Medical Register (doctor), ICAI member search (CA), SEBI registered intermediaries (advisers/brokers).
- **US**: a state contractor board (e.g. California CSLB licence lookup) and/or a state medical board.
- Target: **2 rock-solid (Must), 4–5 total (Should)**, spanning ≥2 countries and ≥3 record types.

## PIPELINE (`pipeline.py`) — deterministic order
1. Validate input; map `(jurisdiction, record_type)` → adapters via `registry_loader`. None → `UNSUPPORTED` + list supported.
2. For each adapter: **TF.agent_run** (search_url, agent_goal, result_schema) → candidate rows. Catch `TFBlocked` → mark adapter `BLOCKED` in notes.
3. `names.resolve(query.name, candidates)` (+identifier exact match) → MATCH/MULTIPLE/NO_MATCH.
4. If MATCH: **TF.fetch(detail_url, render=True, screenshot=True)** → parse via selectors → fallback Groq extract (validated) → build Evidence (sha256 of raw html).
5. `status_map` normalize (unknown raw → `UNKNOWN`, keep raw).
6. Corroborate across adapters sharing record_type; set `conflicts`.
7. Persist; return.

## MONITOR (`watch.py`) — must be reliable
- `/watch` → `tf.monitor_create(detail_url, interval)`; store `monitor_id ↔ record_id`.
- Poller (every 5–10 min) reads monitor events → on event: re-run steps 4–5 for that record only → compare tracked fields (`status, expiry_date, licence_number, disciplinary_actions`) → if changed insert `changes` row `{record_id, field, old, new, detected_at, evidence}` and POST `notify_url` if set.
- Status transition table (deterministic): any `*→SUSPENDED|REVOKED|EXPIRED` = severity `high`.
- Demo reliability: provide `/dev/simulate-change` ONLY for local tests (clearly labelled), and in the demo show one **real** monitor event if achievable; never present simulation as real.

## TESTS (`tests/`)
12 golden cases in `fixtures/`: 4 MATCH (different countries/types), 3 MULTIPLE (common names / variants like `P. Patel` vs `Prit Patel`), 3 NO_MATCH, 2 BLOCKED/UNSUPPORTED. Unit tests for `names.resolve` thresholds, status_map, schema validation. Live tests marked `@pytest.mark.live`.

## UI (Base44 prompt — paste as-is)
> Build a clean, trust-focused web app "TinyVerify". Page 1 "Verify": form (name, record type dropdown, jurisdiction dropdown) → calls POST {API_URL}/verify. Show result as a card: big status badge (green ACTIVE, amber EXPIRED, red SUSPENDED/REVOKED, grey UNKNOWN), licence number, expiry date, disciplinary actions list, and an "Evidence" row with source link (opens new tab), "checked at" UTC time, SHA256 short hash, screenshot thumbnail. If outcome=MULTIPLE show a ranked candidate list with a "This one" button calling /verify/select. If NO_MATCH show which registries were searched. "Watch this record" button calls POST /watch. Page 2 "Watchlist": table of watched records + last checked + status. Page 3 "Changes": feed from GET /changes with old→new status chips. Page 4 "Registries": GET /registries as cards with health dots. Style: minimal, compliance-grade, no gradients.

## README MUST INCLUDE (judge checklist)
- How **Agent** (drives search forms, returns candidate rows), **Fetch** (renders detail page → evidence HTML + screenshot), **Monitor** (watches detail page → triggers re-verify and change flag) are used.
- Adapter authoring guide (how to add a registry in 15 min).
- Sample outputs for MATCH / MULTIPLE / NO_MATCH / change event.
- Limits: which registries unsupported and why.

## MoSCoW
- **Must:** 2 working adapters, /verify schema, ambiguity (3 outcomes), evidence receipt, /watch + poller + /changes, README, 3 demo subjects of different types/places
- **Should:** 4–5 adapters, corroboration, Base44 UI, `/endpoint-usage`, test suite
- **Could:** Search endpoint for adapter discovery (4th endpoint, only if it does real work), batch CSV `/verify/batch`, Telegram alerts
- **Won't:** captcha solving, login-walled registries, storing sensitive personal data beyond public record fields

## BUILD ORDER (Nov 5–9)
D1 adapter loader + first adapter end-to-end → D2 second adapter + names.resolve + tests → D3 monitor/poller/changes → D4 UI + README + demo recordings → D5 extra adapters.
