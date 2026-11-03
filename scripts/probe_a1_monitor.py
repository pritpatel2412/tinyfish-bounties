"""
scripts/probe_a1_monitor.py
Probe A1: Monitor Type & Change Semantics
Tests Page Monitor creation, baseline run, run with NO change, and run WITH change on a controlled mutable URL.
"""

import os
import json
import time
import httpx
from dotenv import dotenv_values

cfg = dotenv_values(".env")
api_key = cfg.get("TINYFISH_API_KEY")

print("=== Starting Probe A1: Monitor Type & Change Semantics ===")

# Step 1: Create a mutable page via webhook.site
print("1. Creating mutable page on webhook.site...")
tok_resp = httpx.post("https://webhook.site/token", json={}, timeout=15)
tok_data = tok_resp.json()
token_uuid = tok_data["uuid"]
page_url = f"https://webhook.site/{token_uuid}"
print(f"   Created page URL: {page_url}")

# Initial content
v1_html = "<html><body><h1>Acme SaaS Pricing</h1><p>Starter: $10/mo</p><p>Pro: $20/mo</p></body></html>"
up1 = httpx.put(
    f"https://webhook.site/token/{token_uuid}",
    json={"default_status": 200, "default_content": v1_html, "default_content_type": "text/html"},
    timeout=15
)
print(f"   Set initial v1 content: status={up1.status_code}")

# Step 2: Create a monitor on the page
print("\n2. Creating Monitor with purpose 'A plan price or plan name changes'...")
create_payload = {
    "type": "fetch",
    "name": "Probe A1 Price Monitor",
    "config": {
        "url": page_url,
        "format": "markdown"
    },
    "schedule_cron": "0 * * * *",
    "purpose": "A plan price or plan name changes"
}

create_resp = httpx.post(
    "https://agent.tinyfish.ai/v1/monitors",
    headers={"X-API-Key": api_key, "Content-Type": "application/json"},
    json=create_payload,
    timeout=30
)
print(f"   Create response HTTP {create_resp.status_code}:")
create_json = create_resp.json()
print("   Create keys:", list(create_json.keys()))
monitor_id = create_json.get("monitor", {}).get("id")
baseline_run = create_json.get("run", {})
print(f"   Monitor ID: {monitor_id}")
print(f"   Baseline Run keys: {list(baseline_run.keys())}")
print(f"   Baseline Run payload: {json.dumps(baseline_run, indent=2)}")

try:
    # Step 3: Trigger Run 1 (NO CHANGE)
    print("\n3. Triggering Run 1 (NO CHANGE)...")
    run1_resp = httpx.post(
        f"https://agent.tinyfish.ai/v1/monitors/{monitor_id}/runs",
        headers={"X-API-Key": api_key, "Content-Type": "application/json"},
        json={},
        timeout=30
    )
    print(f"   Run 1 HTTP {run1_resp.status_code}:")
    run1_json = run1_resp.json()
    print("   Run 1 payload:")
    print(json.dumps(run1_json, indent=2))

    # Step 4: Update page content to V2 (CHANGED)
    print("\n4. Updating page to V2 (Price change: Starter $15/mo, Pro $35/mo)...")
    v2_html = "<html><body><h1>Acme SaaS Pricing</h1><p>Starter: $15/mo</p><p>Pro: $35/mo</p></body></html>"
    up2 = httpx.put(
        f"https://webhook.site/token/{token_uuid}",
        json={"default_status": 200, "default_content": v2_html, "default_content_type": "text/html"},
        timeout=15
    )
    print(f"   Updated v2 content: status={up2.status_code}")
    # Brief sleep to ensure upstream CDN/fetch sees update
    time.sleep(2)

    # Step 5: Trigger Run 2 (WITH CHANGE)
    print("\n5. Triggering Run 2 (WITH CHANGE)...")
    run2_resp = httpx.post(
        f"https://agent.tinyfish.ai/v1/monitors/{monitor_id}/runs",
        headers={"X-API-Key": api_key, "Content-Type": "application/json"},
        json={},
        timeout=30
    )
    print(f"   Run 2 HTTP {run2_resp.status_code}:")
    run2_json = run2_resp.json()
    print("   Run 2 payload:")
    print(json.dumps(run2_json, indent=2))

    # Step 6: Comparison analysis
    print("\n=== A1 Run Analysis ===")
    r1 = run1_json.get("run", {})
    r2 = run2_json.get("run", {})
    print(f"Run 1 is_baseline: {r1.get('is_baseline')}")
    print(f"Run 2 is_baseline: {r2.get('is_baseline')}")
    for k in ["changed", "diff", "change", "has_change", "summary", "evaluation"]:
        print(f"Field '{k}': in Run 1? {k in r1}; in Run 2? {k in r2}")

finally:
    # Cleanup
    print("\n6. Cleaning up monitor...")
    del_resp = httpx.delete(
        f"https://agent.tinyfish.ai/v1/monitors/{monitor_id}",
        headers={"X-API-Key": api_key},
        timeout=15
    )
    print(f"   Deleted monitor: HTTP {del_resp.status_code} {del_resp.text}")
