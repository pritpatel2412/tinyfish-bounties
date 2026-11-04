"""
scripts/migrate_all_monitors_to_webhook.py
Part B2: Migrates all tracked pages to new TinyFish monitors pointing to the deployed Supabase Edge Function.
Staggers creations 1.0s apart, updates SQLite and Supabase store mappings.
"""
import os
import sys
import time
from dotenv import dotenv_values

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from tinycore.tf_client import TF
from tinycore.store import get_store

def main():
    cfg = dotenv_values(".env")
    tf_key = cfg.get("TINYFISH_API_KEY")
    sb_url = cfg.get("SUPABASE_URL")
    webhook_secret = cfg.get("WEBHOOK_SECRET")
    fn_url = f"{sb_url}/functions/v1/tf-webhook?secret={webhook_secret}"

    tf = TF(api_key=tf_key)
    store_sqlite = get_store("sqlite")
    store_supa = get_store("supabase")

    pages = [p for p in store_sqlite.list_pages() if not p.get("is_test")]
    print(f"Total non-test pages to process: {len(pages)}")
    print(f"Target Webhook URL: {fn_url.split('?')[0]}?secret=***")

    success_count = 0
    fail_count = 0

    for i, page in enumerate(pages, 1):
        old_mid = page.get("monitor_id")
        title = page.get("title") or "Page"
        url = page.get("url")
        cron = page.get("schedule_cron") or "0 */6 * * *"

        # Check if already pointing to the new Edge Function
        if old_mid:
            try:
                m_info = tf._execute_http("GET", f"https://agent.tinyfish.ai/v1/monitors/{old_mid}", timeout=15.0).json()
                if "functions/v1/tf-webhook" in (m_info.get("webhook_url") or ""):
                    print(f"[{i}/{len(pages)}] '{title[:25]}' already configured with Edge Function ({old_mid}). Skipping.")
                    success_count += 1
                    continue
            except Exception:
                pass

        print(f"\n[{i}/{len(pages)}] Migrating: {title[:30]} ({url})")
        print(f" - Deleting old monitor: {old_mid}")
        if old_mid:
            try:
                tf.monitor_delete(old_mid)
            except Exception as e:
                print(f"   (Old monitor deletion note: {e})")

        time.sleep(1.0)

        print(f" - Creating new monitor (cron: {cron})...")
        try:
            res = tf.monitor_create(
                url=url,
                schedule_cron=cron,
                webhook_url=fn_url,
                name=f"TM: {title[:28]}",
                app="time_machine"
            )
            new_mid = res.monitor_id
            print(f"   Success! New monitor ID: {new_mid}")

            # Update DB stores
            store_sqlite.update_page(page["id"], {"monitor_id": new_mid})
            try:
                store_supa.update_page(page["id"], {"monitor_id": new_mid})
            except Exception as sb_err:
                print(f"   Supabase store update warning: {sb_err}")

            success_count += 1
        except Exception as err:
            print(f"   FAILED to recreate monitor for {url}: {err}")
            fail_count += 1

        time.sleep(1.0)

    print("\n" + "=" * 60)
    print(f"MIGRATION COMPLETE: {success_count} succeeded, {fail_count} failed.")
    print("=" * 60)

if __name__ == "__main__":
    main()
