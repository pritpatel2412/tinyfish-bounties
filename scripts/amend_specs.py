"""
scripts/amend_specs.py
Applies Gate 2 amendments to specs in docs/.
"""

import os
import re

# 1. Amend 00_tinycore_SPEC.md
p00 = os.path.join("docs", "00_tinycore_SPEC.md")
with open(p00, "r", encoding="utf-8") as f:
    c00 = f.read()

old_tf = """### `tf_client.py`
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
- `TODO(confirm)`: endpoint paths, auth, payload shapes from TINYFISH_NOTES.md."""

new_tf = """### `tf_client.py`
```python
class TF:
    def fetch(self, url:str, format:str="markdown") -> FetchResult   # text, status, latency_ms
    def agent_run(self, url:str, goal:str, output_schema:dict) -> AgentResult  # data(dict), run_id, num_steps
    def screenshot(self, url:str) -> bytes  # Primary: Browser API CDP session (cheaper, full-page); fallback: agent step
    def monitor_create(self, url:str, interval_minutes:int, name:str, webhook_url:str|None=None, purpose:str|None=None) -> tuple[str, dict]  # (monitor_id, baseline_run)
    def monitor_trigger_run(self, monitor_id:str) -> dict  # run object
    def monitor_delete(self, monitor_id:str) -> None
    def get_run(self, run_id:str, screenshots:str="url") -> dict
```
- Retries: 3x exponential backoff on 429/5xx/timeouts. Typed errors: `TFRateLimited`, `TFBlocked`(captcha/403), `TFTimeout`, `TFSchemaMismatch`.
- All calls logged to `data/tf_calls.jsonl` with cost estimate.

### `ledger.py` (CostLedger & MonitorBudget)
- `MonitorBudget`: Hard account cap = 50 monitors. Per-app quotas: Time Machine <= 30, TinyVerify <= 10, Site2MCP <= 2 (total 42, 8 buffer). Rejects allocations beyond quota.
- `CostLedger`: Tracks cumulative spend across calls. Daily budget cap env `MAX_DAILY_USD` (default $2.50). Aborts with `BudgetExceeded` when daily spend hits threshold. Persisted in SQLite."""

old_poller = """### `monitor_poller.py`
Cron loop (APScheduler) polling `tf.monitor_events` every 5–10 min for all registered monitors. **Do not rely on webhooks alone** (free hosts sleep). If webhook exists, use it as accelerator + poll as source of truth. Idempotent by `(monitor_id, event_id|content_hash)`."""

new_poller = """### `events.py` & `monitor_poller.py`
- Event source: reads `monitor_events` table in Supabase via REST (`processed=false`), ensuring sleeping free hosts never miss events.
- Fallback manual checks: Cron loop (APScheduler) can invoke `tf.monitor_trigger_run(monitor_id)` for high-priority pages.
- Processing is idempotent by `(monitor_id, run_id|content_hash)`. Marks `processed=true` upon completion."""

c00_mod = c00.replace(old_tf.replace("\r\n", "\n"), new_tf.replace("\r\n", "\n"))
c00_mod = c00_mod.replace(old_poller.replace("\r\n", "\n"), new_poller.replace("\r\n", "\n"))

with open(p00, "w", encoding="utf-8") as f:
    f.write(c00_mod)
print("Updated 00_tinycore_SPEC.md")


# 2. Amend 01_TinyVerify_SPEC.md
p01 = os.path.join("docs", "01_TinyVerify_SPEC.md")
with open(p01, "r", encoding="utf-8") as f:
    c01 = f.read()

c01_mod = c01.replace(
    'search_url: "https://..."',
    'search_mode: "url_template"     # "url_template" (Fetch-first) | "agent" (interaction required)\n  search_url: "https://..."'
)
c01_mod = c01_mod.replace(
    '2. For each adapter: **TF.agent_run** (search_url, agent_goal, result_schema) → candidate rows. Catch `TFBlocked` → mark adapter `BLOCKED` in notes.',
    '2. For each adapter: If `search_mode == "url_template"`, perform **Fetch-first** search and parse candidate rows deterministically in Python. Fallback to **TF.agent_run** only if interaction/form submission is required. Catch `TFBlocked` → mark adapter `BLOCKED` in notes.'
)
c01_mod = c01_mod.replace(
    '4. If MATCH: **TF.fetch(detail_url, render=True, screenshot=True)**',
    '4. If MATCH: **TF.fetch(detail_url)** + **TF.screenshot(detail_url)**'
)
c01_mod = c01_mod.replace(
    "- `/watch` → `tf.monitor_create(detail_url, interval)`; store `monitor_id ↔ record_id`.",
    "- `/watch` → Check `MonitorBudget` (cap ≤ 10 watches; reject with clear 429 error if exceeded) → `tf.monitor_create(detail_url, interval)` → store `monitor_id ↔ record_id`."
)
with open(p01, "w", encoding="utf-8") as f:
    f.write(c01_mod)
print("Updated 01_TinyVerify_SPEC.md")


# 3. Amend 02_TimeMachine_SPEC.md
p02 = os.path.join("docs", "02_TimeMachine_SPEC.md")
with open(p02, "r", encoding="utf-8") as f:
    c02 = f.read()

c02_mod = c02.replace(
    "Our poller only reads Monitor events; on an event we use Fetch/screenshot to capture the content.",
    "Our poller reads Monitor events from the Supabase `monitor_events` webhook table (and manual run triggers); on an event we use Fetch to capture content and take a screenshot via Browser CDP ONLY for v1 and non-noise changes."
)
c02_mod = c02_mod.replace(
    "2. Poller (every 5–10 min): `tf.monitor_events(monitor_id, since=last)` → for each new event:",
    "2. Poller (every 5–10 min): Read unprocessed events from Supabase `monitor_events` table (monitor budget ≤30 pages, default interval 360 min, ≥30 min) → for each event:"
)
c02_mod = c02_mod.replace(
    "a. `tf.fetch(url, render=True, screenshot=True)`",
    "a. `tf.fetch(url)`"
)
c02_mod = c02_mod.replace(
    "e. else: store screenshot, new `versions` row, `diffs` row",
    "e. else: capture screenshot via `tf.screenshot(url)` (only for non-noise changes), store screenshot, new `versions` row, `diffs` row"
)
with open(p02, "w", encoding="utf-8") as f:
    f.write(c02_mod)
print("Updated 02_TimeMachine_SPEC.md")


# 4. Amend 03_SiteToMCP_SPEC.md
p03 = os.path.join("docs", "03_SiteToMCP_SPEC.md")
with open(p03, "r", encoding="utf-8") as f:
    c03 = f.read()

c03_mod = c03.replace(
    "- **TinyFish Monitor** on the listing page (3rd endpoint): event → run healthcheck immediately → write `health.json` + flip `/health` to `DEGRADED`.",
    "- **TinyFish Monitor** on listing page (≤2 monitors quota from MonitorBudget): webhook event from Supabase `monitor_events` → run healthcheck immediately → write `health.json` + flip `/health` to `DEGRADED`."
)
with open(p03, "w", encoding="utf-8") as f:
    f.write(c03_mod)
print("Updated 03_SiteToMCP_SPEC.md")
