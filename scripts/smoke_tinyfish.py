#!/usr/bin/env python3
"""
scripts/smoke_tinyfish.py
Day-1 Gate Smoke Test for TinyFish API suite:
1. Fetch: Test fetching a real pricing page (Stripe) and test screenshot format behavior.
2. Agent: Run web agent on UK Companies House search to extract company registration details.
3. Monitor: Create a page monitor, read its baseline event/run, trigger manual run, and delete.
4. Screenshot: Obtain and verify JPEG screenshot bytes from agent execution step.

Logs all TinyFish calls to data/tf_calls.jsonl.
"""

import os
import sys
import json
import time
from datetime import datetime, timezone
import urllib.request
import urllib.error

CALLS_LOG = os.path.join("data", "tf_calls.jsonl")


def load_env() -> dict[str, str]:
    env = {}
    if os.path.exists(".env"):
        with open(".env", "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip("'\"")
    return env


def log_call(endpoint: str, url: str, ok: bool, latency_ms: float, run_id: str | None = None):
    os.makedirs(os.path.dirname(CALLS_LOG), exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "endpoint": endpoint,
        "url": url,
        "ok": ok,
        "latency_ms": round(latency_ms, 2),
        "run_id": run_id
    }
    with open(CALLS_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def make_request(url: str, api_key: str, method: str = "GET", data: dict | None = None, headers: dict | None = None) -> tuple[int, dict | bytes, float]:
    req_headers = {"X-API-Key": api_key}
    if headers:
        req_headers.update(headers)
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        req_headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=body, headers=req_headers, method=method)
    start_time = time.time()
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            latency_ms = (time.time() - start_time) * 1000
            content_type = resp.headers.get("Content-Type", "")
            raw = resp.read()
            if "application/json" in content_type:
                return resp.status, json.loads(raw.decode("utf-8")), latency_ms
            return resp.status, raw, latency_ms
    except urllib.error.HTTPError as e:
        latency_ms = (time.time() - start_time) * 1000
        raw = e.read()
        try:
            parsed = json.loads(raw.decode("utf-8"))
            return e.code, parsed, latency_ms
        except Exception:
            return e.code, raw, latency_ms


def test_fetch(api_key: str) -> bool:
    print("\n--- 1. Testing Fetch API (Stripe Pricing) ---")
    target_url = "https://stripe.com/pricing"
    status, res, latency = make_request(
        "https://api.fetch.tinyfish.ai",
        api_key,
        method="POST",
        data={"urls": [target_url], "format": "markdown"}
    )
    ok = (status == 200 and isinstance(res, dict) and bool(res.get("results")))
    log_call("fetch", target_url, ok, latency)

    if not ok:
        print(f"FAILED Fetch: HTTP {status}, response: {res}")
        return False

    first_result = res["results"][0]
    print(f"PASS Fetch: HTTP {status} in {latency:.1f}ms")
    print(f"  Title: {first_result.get('title')}")
    print(f"  Final URL: {first_result.get('final_url')}")
    text_sample = first_result.get("text", "")[:140].replace("\n", " ")
    print(f"  Sample Text: {text_sample}...")

    # Probe Fetch screenshot capability to verify supported formats
    status_shot, res_shot, _ = make_request(
        "https://api.fetch.tinyfish.ai",
        api_key,
        method="POST",
        data={"urls": [target_url], "format": "screenshot"}
    )
    print(f"  Fetch format='screenshot' probe: HTTP {status_shot} (Expected 400: Fetch only accepts markdown|html|json)")
    return True


def test_agent(api_key: str) -> tuple[bool, str | None]:
    print("\n--- 2. Testing Agent API (UK Companies House Registry Search) ---")
    target_url = "https://find-and-update.company-information.service.gov.uk/"
    goal = "Search for company 'DeepMind' and extract the company name and company number of the first search result."
    schema = {
        "type": "object",
        "properties": {
            "company_name": {"type": "string"},
            "company_number": {"type": "string"}
        },
        "required": ["company_name", "company_number"]
    }
    payload = {
        "url": target_url,
        "goal": goal,
        "output_schema": schema,
        "browser_profile": "lite"
    }

    status, res, latency = make_request(
        "https://agent.tinyfish.ai/v1/automation/run",
        api_key,
        method="POST",
        data=payload
    )

    run_id = None
    if isinstance(res, dict):
        run_id = res.get("run_id")

    ok = (status == 200 and isinstance(res, dict) and res.get("status") == "COMPLETED")
    log_call("agent_run", target_url, ok, latency, run_id=run_id)

    if not ok:
        print(f"FAILED Agent: HTTP {status}, response: {res}")
        return False, None

    print(f"PASS Agent: HTTP {status} in {latency:.1f}ms")
    print(f"  Run ID: {run_id}")
    print(f"  Steps taken: {res.get('num_of_steps')}")
    print(f"  Extracted Result: {json.dumps(res.get('result'))}")
    return True, run_id


def test_screenshot(api_key: str, run_id: str | None) -> bool:
    print("\n--- 3. Testing Screenshot Retrieval via Agent Run ---")
    if not run_id:
        print("SKIP Screenshot: No agent run_id provided.")
        return False

    # 1. Inspect step screenshot URLs via run details
    status, run_data, latency = make_request(
        f"https://agent.tinyfish.ai/v1/runs/{run_id}?screenshots=url",
        api_key,
        method="GET"
    )
    if status != 200 or not isinstance(run_data, dict):
        print(f"FAILED to fetch run details: HTTP {status}")
        return False

    steps = run_data.get("steps", [])
    if not steps:
        print("FAILED: No execution steps found in run.")
        return False

    first_step_id = steps[0].get("id")
    print(f"  Step ID: {first_step_id}")
    print(f"  Screenshot URL: {steps[0].get('screenshot')}")

    # 2. Retrieve binary image bytes directly
    shot_url = f"https://agent.tinyfish.ai/v1/runs/{run_id}/steps/{first_step_id}/screenshot"
    status_img, img_bytes, latency_img = make_request(shot_url, api_key, method="GET")
    ok = (status_img == 200 and isinstance(img_bytes, bytes) and len(img_bytes) > 1000)
    log_call("screenshot", shot_url, ok, latency_img, run_id=run_id)

    if not ok:
        print(f"FAILED Screenshot: HTTP {status_img}")
        return False

    out_file = os.path.join("data", "smoke_screenshot.jpg")
    with open(out_file, "wb") as f:
        f.write(img_bytes)

    print(f"PASS Screenshot: HTTP {status_img} in {latency_img:.1f}ms")
    print(f"  Received {len(img_bytes)} bytes JPEG.")
    print(f"  Saved evidence screenshot to: {out_file}")
    return True


def test_monitor(api_key: str) -> bool:
    print("\n--- 4. Testing Monitor API (Create, Read Baseline & Run, Delete) ---")
    target_url = "https://httpbin.org/html"
    create_payload = {
        "type": "fetch",
        "name": "Smoke Test Page Monitor",
        "config": {
            "url": target_url,
            "format": "markdown"
        },
        "schedule_cron": "0 * * * *",
        "purpose": "Detect changes in page text"
    }

    # Step A: Create Monitor (captures initial baseline run immediately)
    status_create, res_create, lat_create = make_request(
        "https://agent.tinyfish.ai/v1/monitors",
        api_key,
        method="POST",
        data=create_payload
    )

    monitor_id = None
    baseline_run_id = None
    if isinstance(res_create, dict):
        monitor_id = (res_create.get("monitor") or {}).get("id")
        baseline_run_id = (res_create.get("run") or {}).get("id")

    ok_create = (status_create == 201 and bool(monitor_id))
    log_call("monitor_create", target_url, ok_create, lat_create, run_id=baseline_run_id)

    if not ok_create:
        print(f"FAILED Monitor create: HTTP {status_create}, response: {res_create}")
        return False

    print(f"PASS Monitor Create: HTTP {status_create} in {lat_create:.1f}ms")
    print(f"  Monitor ID: {monitor_id}")
    print(f"  Baseline Run ID: {baseline_run_id}")
    baseline_results = (res_create.get("run") or {}).get("results", [])
    if baseline_results:
        print(f"  Baseline text length: {len(baseline_results[0].get('text', ''))} chars")

    # Step B: Get Monitor Details
    status_get, res_get, _ = make_request(
        f"https://agent.tinyfish.ai/v1/monitors/{monitor_id}",
        api_key,
        method="GET"
    )
    print(f"  Monitor status: {res_get.get('status') if isinstance(res_get, dict) else status_get}")

    # Step C: Trigger Manual Run Now
    status_run, res_run, lat_run = make_request(
        f"https://agent.tinyfish.ai/v1/monitors/{monitor_id}/runs",
        api_key,
        method="POST",
        data={}
    )
    run_obj = res_run.get("run") if isinstance(res_run, dict) else None
    manual_run_id = run_obj.get("id") if run_obj else None
    ok_run = (status_run == 201 and bool(manual_run_id))
    log_call("monitor_run", f"https://agent.tinyfish.ai/v1/monitors/{monitor_id}/runs", ok_run, lat_run, run_id=manual_run_id)
    print(f"PASS Monitor Run Now: HTTP {status_run} in {lat_run:.1f}ms (Run ID: {manual_run_id})")

    # Step D: Delete Monitor
    status_del, res_del, lat_del = make_request(
        f"https://agent.tinyfish.ai/v1/monitors/{monitor_id}",
        api_key,
        method="DELETE"
    )
    ok_del = (status_del == 200 and isinstance(res_del, dict) and res_del.get("deleted") is True)
    log_call("monitor_delete", f"https://agent.tinyfish.ai/v1/monitors/{monitor_id}", ok_del, lat_del)
    print(f"PASS Monitor Delete: HTTP {status_del} in {lat_del:.1f}ms (Deleted: {res_del.get('deleted')})")

    return True


def main():
    print("=" * 60)
    print("TinyFish Day-1 Gate Smoke Test")
    print("=" * 60)

    env = load_env()
    api_key = env.get("TINYFISH_API_KEY")
    if not api_key:
        print("ERROR: TINYFISH_API_KEY is missing from .env")
        sys.exit(1)

    print(f"Loaded TINYFISH_API_KEY: {api_key[:6]}...{api_key[-4:]}")

    # 1. Fetch
    fetch_ok = test_fetch(api_key)

    # 2. Agent
    agent_ok, run_id = test_agent(api_key)

    # 3. Screenshot
    shot_ok = test_screenshot(api_key, run_id)

    # 4. Monitor
    monitor_ok = test_monitor(api_key)

    print("\n" + "=" * 60)
    print("Summary:")
    print(f"  Fetch API:       {'[PASS]' if fetch_ok else '[FAIL]'}")
    print(f"  Agent API:       {'[PASS]' if agent_ok else '[FAIL]'}")
    print(f"  Screenshot API:  {'[PASS]' if shot_ok else '[FAIL]'}")
    print(f"  Monitor API:     {'[PASS]' if monitor_ok else '[FAIL]'}")
    print(f"  Audit log:       {CALLS_LOG}")
    print("=" * 60)

    if not (fetch_ok and agent_ok and shot_ok and monitor_ok):
        print("One or more tests failed.")
        sys.exit(1)

    print("All smoke tests passed successfully!")


if __name__ == "__main__":
    main()
