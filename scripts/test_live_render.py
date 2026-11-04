import httpx
from dotenv import dotenv_values

cfg = dotenv_values(".env")
base_url = "https://timemachine-api.onrender.com"
admin_token = cfg.get("ADMIN_TOKEN", "adm_25unbIitEHtiHUeCAIv97OzE")
client = httpx.Client(timeout=30.0)

print(f"Testing live service at: {base_url}")

# 1. Test /health
r_health = client.get(f"{base_url}/health")
print("1. /health:", r_health.status_code, r_health.json())

# 2. Test /pages
r_pages = client.get(f"{base_url}/pages")
pages_data = r_pages.json()
page_count = len(pages_data) if isinstance(pages_data, list) else pages_data.get("count")
print("2. /pages:", r_pages.status_code, f"Count: {page_count}")

# 3. Test /admin/health
r_admin = client.get(f"{base_url}/admin/health", headers={"X-Admin-Token": admin_token})
admin_json = r_admin.json()
print("3. /admin/health:", r_admin.status_code, f"Monitors Active: {admin_json.get('monitors_active')}, Daily Spend: ${admin_json.get('daily_spend_usd')}")

# 4. Test /export.json
r_export = client.get(f"{base_url}/export.json")
print("4. /export.json:", r_export.status_code, f"Export size: {len(r_export.text)} bytes")

# 5. Test /as-of query on a real tracked page
sample_page = pages_data[0] if isinstance(pages_data, list) and pages_data else {}
sample_url = sample_page.get("url")
if sample_url:
    r_asof = client.get(f"{base_url}/as-of?url={sample_url}&at=2026-11-04T12:00:00Z")
    print("5. /as-of:", r_asof.status_code, f"Version: {r_asof.json().get('version_no')}, Content Hash: {r_asof.json().get('content_hash', '')[:12]}...")
