import httpx
from dotenv import dotenv_values

cfg = dotenv_values(".env")
sb_url = cfg.get("SUPABASE_URL", "https://xlljoenoqktpwqfvpidh.supabase.co")
sb_key = cfg.get("SUPABASE_KEY")
webhook_secret = cfg.get("WEBHOOK_SECRET", "whsec_Za8cxCiPCOYaaQjygwgiWll6")

print(f"Checking Supabase URL: {sb_url}")

# 1. Check monitor_events table
headers = {
    "apikey": sb_key or "",
    "Authorization": f"Bearer {sb_key or ''}"
}
r_table = httpx.get(f"{sb_url}/rest/v1/monitor_events?select=*", headers=headers, timeout=10.0)
print(f"1. Table monitor_events: HTTP {r_table.status_code}")
if r_table.status_code == 200:
    print(f"   Row count: {len(r_table.json())}")
else:
    print(f"   Response: {r_table.text}")

# 2. Check Edge Function with wrong secret
fn_url = f"{sb_url}/functions/v1/tf-webhook"
r_wrong = httpx.post(f"{fn_url}?secret=deliberately_wrong_secret", json={"test": True}, timeout=10.0)
print(f"2. Edge Function (wrong secret): HTTP {r_wrong.status_code} | Body: {r_wrong.text}")

# 3. Check Edge Function with correct secret
r_correct = httpx.post(f"{fn_url}?secret={webhook_secret}", json={"test": True, "ping": "probe"}, timeout=10.0)
print(f"3. Edge Function (correct secret): HTTP {r_correct.status_code} | Body: {r_correct.text}")

