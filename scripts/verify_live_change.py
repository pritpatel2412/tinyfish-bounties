"""
scripts/verify_live_change.py
Stage 7 Live Verification:
1. Sets up mutable test page on webhook.site with initial pricing
2. Adds page via add_page() -> creates TinyFish monitor, captures v1 baseline + screenshot
3. Mutates test page to new pricing ($29 -> $49/mo)
4. Triggers TinyFish monitor run via tf.monitor_run()
5. Passes live run result through processor.py -> detects change -> captures v2 screenshot -> diffs -> Groq classifies
6. Queries /asof before and after change, and /diff?from=1&to=2
7. Saves all sample JSON responses to samples/timemachine/
"""

import os
import sys

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import json
import time
import httpx
from datetime import datetime, timezone, timedelta
from dotenv import dotenv_values
from fastapi.testclient import TestClient

from tinycore.tf_client import TF
from tinycore.store import get_store
from apps.timemachine.capture import add_page
from apps.timemachine.processor import process_single_event
from apps.timemachine.api import app

TOKEN = "4920368c-1f5c-4800-9dd0-ddcea79b4cbf"
TEST_URL = f"https://webhook.site/{TOKEN}"
CONTROL_URL = f"https://webhook.site/token/{TOKEN}"
SAMPLES_DIR = os.path.join("samples", "timemachine")


def set_remote_content(html: str):
    with httpx.Client(timeout=15) as client:
        resp = client.put(CONTROL_URL, json={
            "default_content": html,
            "default_status": 200,
            "default_content_type": "text/html"
        })
        if resp.status_code != 200:
            raise RuntimeError(f"Failed to set remote content: {resp.status_code} {resp.text}")


def main():
    print("=" * 60)
    print(" STAGE 7 — LIVE CHANGE VERIFICATION")
    print("=" * 60)

    cfg = dotenv_values(".env")
    api_key = cfg.get("TINYFISH_API_KEY")
    if not api_key:
        print("ERROR: TINYFISH_API_KEY missing from .env")
        sys.exit(1)

    tf = TF(api_key=api_key)
    store = get_store()
    os.makedirs(SAMPLES_DIR, exist_ok=True)

    # 1. Prepare Initial Baseline Content on webhook.site
    print("\n[Step 1] Setting initial baseline content on webhook.site...")
    v1_html = """<!DOCTYPE html>
<html>
<head><title>Acme Cloud Platform Pricing</title></head>
<body>
  <h1>Acme Cloud Platform Pricing</h1>
  <h2>Standard Plan</h2>
  <p>Price: $29/mo</p>
  <ul>
    <li>5 team members</li>
    <li>10GB cloud storage</li>
    <li>Standard email support</li>
  </ul>
</body>
</html>"""
    set_remote_content(v1_html)
    print(f"  • Baseline content published to {TEST_URL}")

    # Remove existing page if present to guarantee clean baseline
    existing = store.get_page_by_url(TEST_URL)
    if existing:
        print(f"  • Cleaning up prior test page entry {existing['id']}...")
        if hasattr(store, "_conn"):
            conn = store._conn()
            try:
                with conn:
                    conn.execute("DELETE FROM diffs WHERE page_id = ?", (existing["id"],))
                    conn.execute("DELETE FROM checks WHERE page_id = ?", (existing["id"],))
                    conn.execute("DELETE FROM versions WHERE page_id = ?", (existing["id"],))
                    conn.execute("DELETE FROM pages WHERE id = ?", (existing["id"],))
            finally:
                conn.close()

    # 2. Add Page & Capture Baseline V1
    print("\n[Step 2] Adding page to Time Machine (baseline capture + screenshot)...")
    add_res = add_page(
        url=TEST_URL,
        title="Acme Cloud Platform Pricing",
        category="saas_pricing",
        interval_label="2h",
        store=store,
        tf=tf
    )
    page_id = add_res["page"]["id"]
    monitor_id = add_res["page"]["monitor_id"]
    v1 = add_res["version"]
    v1_observed_at = v1["observed_at"]
    print(f"  • Page ID:     {page_id}")
    print(f"  • Monitor ID:  {monitor_id}")
    print(f"  • Version 1:   observed_at={v1_observed_at}, shot={v1.get('screenshot_url')}")

    # 3. Mutate Test Page Content
    print("\n[Step 3] Mutating page content on webhook.site ($29 -> $49/mo)...")
    v2_html = """<!DOCTYPE html>
<html>
<head><title>Acme Cloud Platform Pricing</title></head>
<body>
  <h1>Acme Cloud Platform Pricing</h1>
  <h2>Standard Plan</h2>
  <p>Price: $49/mo</p>
  <ul>
    <li>10 team members</li>
    <li>25GB cloud storage</li>
    <li>Priority 24/7 phone & email support</li>
  </ul>
</body>
</html>"""
    set_remote_content(v2_html)
    print("  • Remote content updated with price increase and feature tier upgrades.")

    # Small delay to ensure DNS / edge caches propagate
    time.sleep(3)

    # 4. Trigger Live Monitor Run via TinyFish
    print("\n[Step 4] Triggering live monitor run via POST /v1/monitors/{id}/runs...")
    try:
        run_res = tf.monitor_trigger_run(monitor_id)
        run_id = run_res.get("id") or run_res.get("run_id")
        print(f"  • Monitor run completed! Run ID: {run_id}")
    except Exception as e:
        print(f"  • Monitor run trigger failed: {e}")
        sys.exit(1)

    # 5. Process the Change Event
    print("\n[Step 5] Processing change event through Time Machine processor...")
    proc_event = {
        "id": f"ev_{run_id}",
        "received_at": datetime.now(timezone.utc).isoformat(),
        "monitor_id": monitor_id,
        "run_id": run_id,
        "payload": run_res
    }
    proc_result = process_single_event(proc_event, store=store, tf=tf)
    print(f"  • Processor outcome: {proc_result.get('outcome')} (Version {proc_result.get('version_no')})")

    # Verify Version 2 in Store
    v2 = store.get_version(page_id, 2)
    if not v2:
        print("  • ERROR: Version 2 was not stored!")
        sys.exit(1)

    print(f"  • Version 2 stored: observed_at={v2['observed_at']}, shot={v2.get('screenshot_url')}")
    print(f"  • Summary: {v2.get('summary')}")
    print(f"  • Classification: {json.dumps(v2.get('classification'), indent=2)}")

    diff_rec = store.get_diff(page_id, 1, 2)
    print(f"  • Diff recorded: {json.dumps(diff_rec.get('diff', {}).get('stats'), indent=2)}")

    # 6. Query API Endpoints & Save Samples
    print("\n[Step 6] Querying API endpoints and saving judge samples to samples/timemachine/...")
    client = TestClient(app)

    # A. GET /pages
    resp_pages = client.get("/pages")
    with open(os.path.join(SAMPLES_DIR, "pages.json"), "w", encoding="utf-8") as f:
        json.dump(resp_pages.json(), f, indent=2)
    print("  -> Saved pages.json")

    # B. GET /pages/{id}/timeline
    resp_timeline = client.get(f"/pages/{page_id}/timeline")
    with open(os.path.join(SAMPLES_DIR, "timeline.json"), "w", encoding="utf-8") as f:
        json.dump(resp_timeline.json(), f, indent=2)
    print("  -> Saved timeline.json")

    # C. GET /pages/{id}/asof (Before Change - Version 1)
    # Target date between v1 and v2
    v1_dt = datetime.fromisoformat(v1_observed_at.replace("Z", "+00:00"))
    before_date = (v1_dt + timedelta(seconds=1)).isoformat()
    resp_asof_before = client.get(f"/pages/{page_id}/asof?date={before_date}")
    with open(os.path.join(SAMPLES_DIR, "asof_before.json"), "w", encoding="utf-8") as f:
        json.dump(resp_asof_before.json(), f, indent=2)
    print("  -> Saved asof_before.json")

    # D. GET /pages/{id}/asof (After Change - Version 2)
    v2_observed_at = v2["observed_at"]
    v2_dt = datetime.fromisoformat(v2_observed_at.replace("Z", "+00:00"))
    after_date = (v2_dt + timedelta(seconds=1)).isoformat()
    resp_asof_after = client.get(f"/pages/{page_id}/asof?date={after_date}")
    with open(os.path.join(SAMPLES_DIR, "asof_after.json"), "w", encoding="utf-8") as f:
        json.dump(resp_asof_after.json(), f, indent=2)
    print("  -> Saved asof_after.json")

    # E. GET /pages/{id}/diff?from=1&to=2
    resp_diff = client.get(f"/pages/{page_id}/diff?from=1&to=2")
    with open(os.path.join(SAMPLES_DIR, "diff_1_to_2.json"), "w", encoding="utf-8") as f:
        json.dump(resp_diff.json(), f, indent=2)
    print("  -> Saved diff_1_to_2.json")

    # F. GET /changes
    resp_changes = client.get("/changes")
    with open(os.path.join(SAMPLES_DIR, "changes.json"), "w", encoding="utf-8") as f:
        json.dump(resp_changes.json(), f, indent=2)
    print("  -> Saved changes.json")

    # G. GET /logs
    resp_logs = client.get("/logs?limit=100")
    with open(os.path.join(SAMPLES_DIR, "logs.json"), "w", encoding="utf-8") as f:
        json.dump(resp_logs.json(), f, indent=2)
    print("  -> Saved logs.json")

    # H. GET /export.json
    resp_export = client.get("/export.json")
    with open(os.path.join(SAMPLES_DIR, "export.json"), "w", encoding="utf-8") as f:
        json.dump(resp_export.json(), f, indent=2)
    print("  -> Saved export.json")

    print("\n" + "=" * 60)
    print(" LIVE VERIFICATION COMPLETE & SUCCESSFUL!")
    print("=" * 60)


if __name__ == "__main__":
    main()
