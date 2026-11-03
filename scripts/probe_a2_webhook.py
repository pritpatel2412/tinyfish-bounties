"""
scripts/probe_a2_webhook.py
Probe A2: Webhook Receiver and Signature Inspection
Tests webhook delivery from TinyFish, captures incoming headers, signature scheme, and payload.
"""

import os
import json
import time
import httpx
from dotenv import dotenv_values

cfg = dotenv_values(".env")
api_key = cfg.get("TINYFISH_API_KEY")

print("=== Starting Probe A2: Webhook Delivery & Inspection ===")

# 1. Create a fresh webhook.site receiver token
tok_resp = httpx.post("https://webhook.site/token", json={}, timeout=15)
tok_data = tok_resp.json()
token_uuid = tok_data["uuid"]
webhook_url = f"https://webhook.site/{token_uuid}"
print(f"1. Receiver webhook URL: {webhook_url}")

# 2. Test Agent run with webhook_url
print("\n2. Triggering Agent run with webhook_url...")
agent_payload = {
    "url": "https://httpbin.org/html",
    "goal": "Extract the heading text from the page",
    "output_schema": {
        "type": "object",
        "properties": {
            "heading": {"type": "string"}
        }
    },
    "webhook_url": webhook_url
}
r_agent = httpx.post(
    "https://agent.tinyfish.ai/v1/automation/run",
    headers={"X-API-Key": api_key, "Content-Type": "application/json"},
    json=agent_payload,
    timeout=120
)
print(f"   Agent run status: {r_agent.status_code}")
agent_data = r_agent.json()
agent_run_id = agent_data.get("run_id")
print(f"   Agent run_id: {agent_run_id}")

# 3. Poll webhook.site for incoming requests
print("\n3. Inspecting received webhook requests on webhook.site...")
received = []
for attempt in range(12):
    time.sleep(2)
    reqs_resp = httpx.get(f"https://webhook.site/token/{token_uuid}/requests", timeout=15)
    if reqs_resp.status_code == 200:
        reqs_data = reqs_resp.json()
        data_list = reqs_data.get("data", [])
        if data_list:
            received = data_list
            break
    print(f"   Waiting for webhook delivery... ({attempt + 1}/12)")

if received:
    print(f"\nSUCCESS: Received {len(received)} webhook request(s)!")
    first_req = received[0]
    print("\nIncoming Webhook Headers:")
    headers = first_req.get("headers", {})
    for h, v in headers.items():
        print(f"  {h}: {v}")
    
    print("\nIncoming Webhook Payload Content:")
    content = first_req.get("content")
    try:
        parsed_payload = json.loads(content)
        print(json.dumps(parsed_payload, indent=2)[:1000])
    except Exception:
        print(content[:500])
else:
    print("\nNo webhook request received during polling window.")
