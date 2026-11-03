"""
tinycore/tf_client.py
Official TinyFish client wrapper enforcing all architectural rules:
- All TinyFish calls pass strictly through this client
- Retries with exponential backoff on 429/5xx/timeouts
- Typed exceptions
- Every call logged to data/tf_calls.jsonl with latency, run_id, cost estimate, and status
- Integrates with CostLedger and MonitorBudget
"""

import os
import json
import time
import asyncio
from datetime import datetime, timezone
from dataclasses import dataclass
import httpx
from playwright.async_api import async_playwright

from tinycore.ledger import CostLedger, MonitorBudget, RATES, BudgetExceeded, QuotaExceeded

CALLS_LOG = os.path.join("data", "tf_calls.jsonl")
FETCH_BASE_URL = "https://api.fetch.tinyfish.ai"
AGENT_BASE_URL = "https://agent.tinyfish.ai"
BROWSER_BASE_URL = "https://api.browser.tinyfish.ai"


class TFError(Exception):
    """Base error for TinyFish API calls."""
    def __init__(self, message: str, status_code: int | None = None, response: dict | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.response = response


class TFRateLimited(TFError):
    """429 Too Many Requests."""
    pass


class TFBlocked(TFError):
    """403 Forbidden or anti-bot challenge."""
    pass


class TFTimeout(TFError):
    """Request timeout."""
    pass


class TFSchemaMismatch(TFError):
    """Returned output does not conform to requested schema."""
    pass


@dataclass
class FetchResult:
    text: str
    status: int
    latency_ms: float
    url: str
    title: str | None = None
    final_url: str | None = None


@dataclass
class AgentResult:
    data: dict
    run_id: str
    num_steps: int
    status: str
    error: str | None = None


def log_tf_call(endpoint: str, url: str, ok: bool, latency_ms: float, run_id: str | None = None, cost_usd: float = 0.0):
    os.makedirs(os.path.dirname(CALLS_LOG), exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "endpoint": endpoint,
        "url": url,
        "ok": ok,
        "latency_ms": round(latency_ms, 2),
        "run_id": run_id,
        "cost_usd": round(cost_usd, 6)
    }
    with open(CALLS_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def minutes_to_cron(minutes: int) -> str:
    """Converts interval in minutes to a 5-field cron expression."""
    if minutes < 30:
        minutes = 30  # Enforce minimum
    if minutes < 60:
        return f"*/{minutes} * * * *"
    hours = minutes // 60
    if hours < 24:
        return f"0 */{hours} * * *"
    days = hours // 24
    return f"0 0 */{days} * *"


class TF:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("TINYFISH_API_KEY")
        if not self.api_key:
            # Fallback reading .env
            env_path = ".env"
            if os.path.exists(env_path):
                from dotenv import dotenv_values
                self.api_key = dotenv_values(env_path).get("TINYFISH_API_KEY")
        if not self.api_key:
            raise ValueError("TINYFISH_API_KEY not configured in environment or .env")
        
        self.headers = {
            "X-API-Key": self.api_key,
            "Content-Type": "application/json"
        }

    def _execute_http(self, method: str, url: str, json_data: dict | None = None, timeout: float = 60.0) -> httpx.Response:
        retries = 3
        backoff = 1.0
        for attempt in range(retries):
            try:
                with httpx.Client(timeout=timeout) as client:
                    resp = client.request(method, url, headers=self.headers, json=json_data)
                    if resp.status_code == 429:
                        if attempt == retries - 1:
                            raise TFRateLimited("Rate limit exceeded after retries", status_code=429)
                        time.sleep(backoff)
                        backoff *= 2
                        continue
                    if resp.status_code == 403:
                        raise TFBlocked(f"Access blocked / forbidden: {resp.text}", status_code=403)
                    if resp.status_code >= 500:
                        if attempt == retries - 1:
                            raise TFError(f"Server error {resp.status_code}: {resp.text}", status_code=resp.status_code)
                        time.sleep(backoff)
                        backoff *= 2
                        continue
                    return resp
            except httpx.TimeoutException as e:
                if attempt == retries - 1:
                    raise TFTimeout(f"Request to {url} timed out: {e}")
                time.sleep(backoff)
                backoff *= 2
            except (httpx.ConnectError, httpx.NetworkError) as e:
                if attempt == retries - 1:
                    raise TFError(f"Network error calling {url}: {e}")
                time.sleep(backoff)
                backoff *= 2
        raise TFError(f"Failed to execute {method} {url} after {retries} retries")

    def fetch(self, url: str, format: str = "markdown") -> FetchResult:
        """Fetch and render page via TinyFish Fetch API (Chromium-rendered, noise-cleaned). Free of charge."""
        CostLedger.check_budget(RATES["fetch"])
        endpoint_url = FETCH_BASE_URL
        t0 = time.time()
        ok = False
        try:
            resp = self._execute_http("POST", endpoint_url, json_data={"urls": [url], "format": format}, timeout=60.0)
            latency_ms = (time.time() - t0) * 1000
            if resp.status_code != 200:
                raise TFError(f"Fetch failed with status {resp.status_code}: {resp.text}", status_code=resp.status_code)
            data = resp.json()
            results = data.get("results", [])
            if not results:
                errors = data.get("errors", [])
                raise TFError(f"Fetch returned no results. Errors: {errors}")
            res0 = results[0]
            ok = True
            CostLedger.record_call("fetch", RATES["fetch"], url)
            log_tf_call("fetch", url, ok, latency_ms, cost_usd=RATES["fetch"])
            return FetchResult(
                text=res0.get("text", ""),
                status=resp.status_code,
                latency_ms=latency_ms,
                url=url,
                title=res0.get("title"),
                final_url=res0.get("final_url")
            )
        except Exception as e:
            latency_ms = (time.time() - t0) * 1000
            log_tf_call("fetch", url, False, latency_ms, cost_usd=0.0)
            raise e

    def agent_run(self, url: str, goal: str, output_schema: dict, timeout: float = 180.0) -> AgentResult:
        """Run an autonomous web agent session to navigate and extract structured data."""
        CostLedger.check_budget(RATES["agent_run_default"])
        endpoint_url = f"{AGENT_BASE_URL}/v1/automation/run"
        payload = {
            "url": url,
            "goal": goal,
            "output_schema": output_schema,
            "browser_profile": "lite"
        }
        t0 = time.time()
        run_id = None
        try:
            resp = self._execute_http("POST", endpoint_url, json_data=payload, timeout=timeout)
            latency_ms = (time.time() - t0) * 1000
            if resp.status_code != 200:
                raise TFError(f"Agent run failed {resp.status_code}: {resp.text}", status_code=resp.status_code)
            data = resp.json()
            run_id = data.get("run_id")
            status = data.get("status")
            num_steps = data.get("num_of_steps", 1)
            cost = num_steps * RATES["agent_step"]
            ok = (status == "COMPLETED")
            CostLedger.record_call("agent_run", cost, f"run_id={run_id}, steps={num_steps}")
            log_tf_call("agent_run", url, ok, latency_ms, run_id=run_id, cost_usd=cost)
            if not ok:
                raise TFError(f"Agent run ended with status '{status}': {data.get('error')}", response=data)
            return AgentResult(
                data=data.get("result", {}),
                run_id=run_id,
                num_steps=num_steps,
                status=status,
                error=data.get("error")
            )
        except Exception as e:
            latency_ms = (time.time() - t0) * 1000
            log_tf_call("agent_run", url, False, latency_ms, run_id=run_id, cost_usd=0.0)
            raise e

    def screenshot(self, url: str) -> bytes:
        """Capture screenshot via Browser API CDP session (Playwright over CDP). Cheaper & full-page capable."""
        CostLedger.check_budget(RATES["screenshot"])
        t0 = time.time()
        session_id = None
        try:
            # 1. Provision browser session
            with httpx.Client(timeout=30) as client:
                resp = client.post(
                    BROWSER_BASE_URL,
                    headers=self.headers,
                    json={"url": url}
                )
            if resp.status_code not in (200, 201):
                raise TFError(f"Failed to provision browser session: {resp.status_code} {resp.text}")
            sess = resp.json()
            session_id = sess.get("session_id")
            cdp_url = sess.get("cdp_url")
            if not cdp_url:
                raise TFError(f"No CDP URL returned: {sess}")

            # 2. Connect Playwright CDP
            async def _grab_shot() -> bytes:
                async with async_playwright() as p:
                    browser = await p.chromium.connect_over_cdp(cdp_url)
                    context = browser.contexts[0] if browser.contexts else await browser.new_context()
                    page = context.pages[0] if context.pages else await context.new_page()
                    await page.goto(url, wait_until="domcontentloaded", timeout=25000)
                    shot = await page.screenshot(type="jpeg", quality=80, timeout=15000, animations="disabled")
                    await browser.close()
                    return shot

            shot_bytes = asyncio.run(_grab_shot())
            latency_ms = (time.time() - t0) * 1000
            duration_minutes = (time.time() - t0) / 60.0
            cost = max(0.0002, duration_minutes * RATES["browser_minute"])
            CostLedger.record_call("browser_screenshot", cost, f"session_id={session_id}")
            log_tf_call("screenshot", url, True, latency_ms, run_id=session_id, cost_usd=cost)
            return shot_bytes
        except Exception as e:
            latency_ms = (time.time() - t0) * 1000
            log_tf_call("screenshot", url, False, latency_ms, run_id=session_id, cost_usd=0.0)
            raise e
        finally:
            if session_id:
                try:
                    with httpx.Client(timeout=10) as client:
                        client.delete(f"{BROWSER_BASE_URL}/{session_id}", headers=self.headers)
                except Exception:
                    pass

    def monitor_create(
        self,
        url: str,
        interval_minutes: int,
        name: str,
        webhook_url: str | None = None,
        purpose: str | None = None,
        app: str | None = None
    ) -> tuple[str, dict]:
        """Create a page monitor and capture the baseline run."""
        if app:
            MonitorBudget.allocate(app, 1)
        CostLedger.check_budget(RATES["monitor_create"])

        cron = minutes_to_cron(interval_minutes)
        payload = {
            "type": "fetch",
            "name": name,
            "config": {
                "url": url,
                "format": "markdown"
            },
            "schedule_cron": cron,
            "purpose": purpose or "Detect material page changes"
        }
        if webhook_url:
            payload["webhook_url"] = webhook_url

        endpoint_url = f"{AGENT_BASE_URL}/v1/monitors"
        t0 = time.time()
        try:
            resp = self._execute_http("POST", endpoint_url, json_data=payload, timeout=60.0)
            latency_ms = (time.time() - t0) * 1000
            if resp.status_code != 201:
                if app:
                    MonitorBudget.release(app, 1)
                raise TFError(f"Monitor creation failed {resp.status_code}: {resp.text}", status_code=resp.status_code)
            data = resp.json()
            monitor_id = data.get("monitor", {}).get("id")
            baseline_run = data.get("run", {})
            CostLedger.record_call("monitor_create", RATES["monitor_create"], f"monitor_id={monitor_id}")
            log_tf_call("monitor_create", url, True, latency_ms, run_id=baseline_run.get("id"), cost_usd=RATES["monitor_create"])
            return monitor_id, baseline_run
        except Exception as e:
            if app:
                MonitorBudget.release(app, 1)
            latency_ms = (time.time() - t0) * 1000
            log_tf_call("monitor_create", url, False, latency_ms, cost_usd=0.0)
            raise e

    def monitor_trigger_run(self, monitor_id: str) -> dict:
        """Trigger an immediate check ('Run Now') on a monitor."""
        CostLedger.check_budget(RATES["monitor_run"])
        endpoint_url = f"{AGENT_BASE_URL}/v1/monitors/{monitor_id}/runs"
        t0 = time.time()
        try:
            resp = self._execute_http("POST", endpoint_url, json_data={}, timeout=60.0)
            latency_ms = (time.time() - t0) * 1000
            if resp.status_code != 201:
                raise TFError(f"Monitor run trigger failed {resp.status_code}: {resp.text}", status_code=resp.status_code)
            data = resp.json()
            run_obj = data.get("run", {})
            CostLedger.record_call("monitor_run", RATES["monitor_run"], f"monitor_id={monitor_id}, run_id={run_obj.get('id')}")
            log_tf_call("monitor_run", endpoint_url, True, latency_ms, run_id=run_obj.get("id"), cost_usd=RATES["monitor_run"])
            return run_obj
        except Exception as e:
            latency_ms = (time.time() - t0) * 1000
            log_tf_call("monitor_run", endpoint_url, False, latency_ms, cost_usd=0.0)
            raise e

    def monitor_delete(self, monitor_id: str, app: str | None = None) -> None:
        """Delete monitor and release allocated budget quota."""
        endpoint_url = f"{AGENT_BASE_URL}/v1/monitors/{monitor_id}"
        t0 = time.time()
        try:
            resp = self._execute_http("DELETE", endpoint_url, timeout=30.0)
            latency_ms = (time.time() - t0) * 1000
            if resp.status_code != 200:
                raise TFError(f"Monitor deletion failed {resp.status_code}: {resp.text}", status_code=resp.status_code)
            if app:
                MonitorBudget.release(app, 1)
            log_tf_call("monitor_delete", endpoint_url, True, latency_ms, cost_usd=0.0)
        except Exception as e:
            latency_ms = (time.time() - t0) * 1000
            log_tf_call("monitor_delete", endpoint_url, False, latency_ms, cost_usd=0.0)
            raise e

    def get_run(self, run_id: str, screenshots: str = "url") -> dict:
        """Retrieve run details and execution steps."""
        endpoint_url = f"{AGENT_BASE_URL}/v1/runs/{run_id}?screenshots={screenshots}"
        t0 = time.time()
        try:
            resp = self._execute_http("GET", endpoint_url, timeout=30.0)
            latency_ms = (time.time() - t0) * 1000
            if resp.status_code != 200:
                raise TFError(f"Failed to get run {run_id}: {resp.status_code} {resp.text}", status_code=resp.status_code)
            data = resp.json()
            log_tf_call("get_run", endpoint_url, True, latency_ms, run_id=run_id, cost_usd=0.0)
            return data
        except Exception as e:
            latency_ms = (time.time() - t0) * 1000
            log_tf_call("get_run", endpoint_url, False, latency_ms, run_id=run_id, cost_usd=0.0)
            raise e
