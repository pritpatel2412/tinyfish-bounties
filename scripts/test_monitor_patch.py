import httpx
import os
import sys
from dotenv import dotenv_values

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from tinycore.tf_client import TF

cfg = dotenv_values(".env")
tf_key = cfg.get("TINYFISH_API_KEY")
tf = TF(api_key=tf_key)

monitors_data = tf.monitors_list()
monitors = monitors_data if isinstance(monitors_data, list) else (monitors_data.get("monitors") or [])
print(f"Total monitors in TinyFish: {len(monitors)}")

test_m = monitors[0]
mid = test_m.get("id") or test_m.get("monitor_id")
print(f"Testing PATCH on monitor: {mid} ({test_m.get('url')})")

fn_url = f"{cfg.get('SUPABASE_URL')}/functions/v1/tf-webhook?secret={cfg.get('WEBHOOK_SECRET')}"

# Test PATCH /v1/monitors/{id}
url = f"https://agent.tinyfish.ai/v1/monitors/{mid}"
headers = {
    "X-API-Key": tf_key,
    "Content-Type": "application/json"
}
payload = {
    "webhook_url": fn_url
}

r = httpx.patch(url, headers=headers, json=payload, timeout=15.0)
print(f"PATCH status code: {r.status_code}")
print(f"PATCH response body: {r.text}")
