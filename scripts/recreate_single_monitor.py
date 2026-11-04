import os
import sys
import time
from dotenv import dotenv_values

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from tinycore.tf_client import TF
from tinycore.store import get_store

cfg = dotenv_values(".env")
tf_key = cfg.get("TINYFISH_API_KEY")
sb_url = cfg.get("SUPABASE_URL")
webhook_secret = cfg.get("WEBHOOK_SECRET")
fn_url = f"{sb_url}/functions/v1/tf-webhook?secret={webhook_secret}"

tf = TF(api_key=tf_key)
store_sqlite = get_store("sqlite")
store_supa = get_store("supabase")

pages = [p for p in store_sqlite.list_pages() if not p.get("is_test")]
page = pages[0]
old_mid = page.get("monitor_id")

print(f"Target Page: {page['title']} ({page['url']})")
print(f"Old Monitor ID: {old_mid}")
print(f"New Webhook Destination: {fn_url.split('?')[0]}?secret=***")

# 1. Delete old monitor
if old_mid:
    try:
        tf.monitor_delete(old_mid)
        print(f"Deleted old monitor {old_mid} successfully.")
    except Exception as e:
        print(f"Warning deleting old monitor: {e}")

time.sleep(1.0)

# 2. Recreate monitor with new webhook_url
print(f"Creating new monitor with cron '{page['schedule_cron']}'...")
res = tf.monitor_create(
    url=page["url"],
    schedule_cron=page["schedule_cron"],
    webhook_url=fn_url,
    name=f"TM: {page['title'][:30]}",
    app="time_machine"
)
new_mid = res.monitor_id
print(f"Created new monitor: {new_mid}")

# 3. Update database
store_sqlite.update_page(page["id"], {"monitor_id": new_mid})
try:
    store_supa.update_page(page["id"], {"monitor_id": new_mid})
    print("Updated monitor_id in Supabase store.")
except Exception as e:
    print(f"Warning updating Supabase: {e}")

print("Single monitor migration test complete!")
