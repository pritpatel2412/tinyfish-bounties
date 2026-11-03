"""
scripts/probe_fetch_webhook.py
Captures the real webhook payload for a type: "fetch" monitor run using smee.io SSE.
Saves the payload to tests/fixtures/webhook_fetch_run.json.
Analyzes whether results[0].text and timestamps are present.
Cleans up the monitor.
"""

import os
import sys
import time
import json
import threading
import httpx
from dotenv import dotenv_values
from tinycore.tf_client import TF

cfg = dotenv_values(".env")
api_key = cfg.get("TINYFISH_API_KEY")
tf = TF(api_key=api_key)

print("=== Probing type: fetch Monitor Webhook Payload ===")

# 1. Obtain fresh smee.io channel
resp = httpx.head("https://smee.io/new", follow_redirects=False, timeout=10)
smee_url = resp.headers.get("location")
if not smee_url:
    print("FAILED to obtain smee.io channel")
    sys.exit(1)
print(f"1. Smee webhook URL: {smee_url}")

# 2. Start listener in background thread
received_events = []
stop_listening = threading.Event()

def sse_listener():
    try:
        with httpx.Client(timeout=120) as client:
            with client.stream("GET", smee_url, headers={"Accept": "text/event-stream"}) as s:
                current_event = {}
                for line in s.iter_lines():
                    if stop_listening.is_set():
                        break
                    line = line.strip()
                    if not line:
                        if current_event.get("data"):
                            try:
                                parsed = json.loads(current_event["data"])
                                received_events.append(parsed)
                                print(f"\n>>> Webhook delivered via Smee! (Total: {len(received_events)})")
                            except Exception:
                                pass
                        current_event = {}
                    elif line.startswith("event:"):
                        current_event["event"] = line[6:].strip()
                    elif line.startswith("data:"):
                        current_event["data"] = line[5:].strip()
    except Exception as e:
        print(f"SSE listener terminated: {e}")

thread = threading.Thread(target=sse_listener, daemon=True)
thread.start()
time.sleep(2)  # Give SSE connection a moment to establish

# 3. Create type: fetch monitor
target_url = "https://httpbin.org/html"
print(f"\n2. Creating fetch monitor for '{target_url}' with webhook '{smee_url}'...")
try:
    mon_res = tf.monitor_create(
        type="fetch",
        config={"url": target_url, "format": "markdown"},
        schedule_cron="*/5 * * * *",
        webhook_url=smee_url
    )
    monitor_id = mon_res.monitor_id
    baseline_run = mon_res.run
    print(f"   Created monitor_id: {monitor_id}")
    print(f"   Baseline run_id: {baseline_run.get('id') if baseline_run else 'none'}")
except Exception as e:
    import traceback
    traceback.print_exc()
    print(f"FAILED to create monitor: {e}")
    stop_listening.set()
    sys.exit(1)

# 4. Trigger a run
print(f"\n3. Triggering immediate run on monitor {monitor_id}...")
try:
    run_obj = tf.monitor_trigger_run(monitor_id)
    print(f"   Manual run triggered: run_id={run_obj.get('id')}")
except Exception as e:
    print(f"   Trigger error: {e}")

# 5. Wait for delivery
print("\n4. Waiting for webhook delivery (up to 45s)...")
for i in range(15):
    if received_events:
        break
    time.sleep(3)
    print(f"   Waiting... ({(i+1)*3}s)")

# 6. Clean up monitor
print(f"\n5. Cleaning up monitor {monitor_id}...")
try:
    tf.monitor_delete(monitor_id)
    print("   Monitor deleted successfully.")
except Exception as e:
    print(f"   Monitor deletion error: {e}")

stop_listening.set()

# 7. Analyze and save
os.makedirs("tests/fixtures", exist_ok=True)
fixture_path = os.path.join("tests", "fixtures", "webhook_fetch_run.json")

if received_events:
    event_data = received_events[0]
    # Smee wraps request in { "headers": ..., "body": ... }
    body = event_data.get("body", event_data)
    with open(fixture_path, "w", encoding="utf-8") as f:
        json.dump(body, f, indent=2)
    print(f"\nSUCCESS: Saved fixture to {fixture_path}")
    print("\nBody keys:", list(body.keys()))
    print("Full payload preview:")
    print(json.dumps(body, indent=2)[:1000])

    # Check for text
    results = body.get("results") or body.get("data", {}).get("results", [])
    if isinstance(results, list) and len(results) > 0:
        has_text = "text" in results[0]
        print(f"\n--> results[0].text present: {has_text}")
        if has_text:
            print(f"--> Text length: {len(results[0].get('text', ''))}")
    else:
        print("\n--> NO results[].text found in webhook body.")

    run_id = body.get("run_id") or body.get("id") or body.get("data", {}).get("run_id")
    print(f"--> run_id: {run_id}")
    ts = body.get("timestamp") or body.get("created_at") or body.get("data", {}).get("created_at")
    print(f"--> timestamp: {ts}")
else:
    print("\nNo webhook event captured during window.")
    if baseline_run:
        print("Using baseline run payload structure as reference:")
        with open(fixture_path, "w", encoding="utf-8") as f:
            json.dump(baseline_run, f, indent=2)
        print(f"Saved baseline run to {fixture_path}")
