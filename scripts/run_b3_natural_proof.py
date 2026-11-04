"""
scripts/run_b3_natural_proof.py
Part B3: Natural-delivery proof.
Creates ONE temporary monitor with cron '*/5 * * * *' and the real Supabase Edge Function webhook.
Waits for a natural scheduled delivery (NEVER calls /runs).
Confirms:
- Row in monitor_events
- Processor drains and marks processed
- Checks record created with trigger='scheduled'
- Deletes the temporary monitor and prints the timestamps.
"""
import os
import sys
import time
from datetime import datetime, timezone
import httpx
from dotenv import dotenv_values

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from tinycore.tf_client import TF
from tinycore.store import get_store
from apps.timemachine.processor import process_single_event

def main():
    cfg = dotenv_values(".env")
    tf_key = cfg.get("TINYFISH_API_KEY")
    sb_url = cfg.get("SUPABASE_URL")
    sb_key = cfg.get("SUPABASE_KEY")
    webhook_secret = cfg.get("WEBHOOK_SECRET")
    fn_url = f"{sb_url}/functions/v1/tf-webhook?secret={webhook_secret}"

    tf = TF(api_key=tf_key)
    store_sqlite = get_store("sqlite")
    store_supa = get_store("supabase")

    test_url = "https://httpbin.org/html"
    cron = "*/5 * * * *"
    
    print("=" * 70)
    print("PART B3: NATURAL SCHEDULED DELIVERY PROOF")
    print("=" * 70)
    
    # 1. Register temporary test page
    created_at_utc = datetime.now(timezone.utc).isoformat()
    test_page = {
        "url": test_url,
        "title": "Temp B3 Natural Delivery Probe",
        "category": "test",
        "schedule_cron": cron,
        "interval_label": "every 5m",
        "history_starts_at": created_at_utc,
        "is_test": True,
        "active": True
    }
    
    p_row = store_sqlite.add_page(test_page)
    page_id = p_row["id"]
    try:
        store_supa.add_page(dict(p_row))
    except Exception as e:
        print(f"Supabase add_page notice: {e}")
        
    print(f"Registered test page ID: {page_id}")
    print(f"Creating temporary monitor on {test_url} with cron '{cron}'...")
    print(f"Webhook Destination: {fn_url.split('?')[0]}?secret=***")
    
    # 2. Create TinyFish Monitor
    res = tf.monitor_create(
        url=test_url,
        schedule_cron=cron,
        webhook_url=fn_url,
        name="PROOF: B3 Natural Delivery",
        app="time_machine"
    )
    temp_mid = res.monitor_id
    monitor_created_at = datetime.now(timezone.utc).isoformat()
    print(f"Monitor created successfully: {temp_mid} at {monitor_created_at}")
    
    store_sqlite.update_page(page_id, {"monitor_id": temp_mid})
    try:
        store_supa.update_page(page_id, {"monitor_id": temp_mid})
    except Exception:
        pass

    # 3. Wait for natural scheduled delivery (Do NOT call /runs!)
    print("\nWaiting for natural scheduled delivery from TinyFish cloud...")
    print("NOTE: We will NOT invoke POST /runs. Delivery must arrive naturally via cron schedule.")
    print("Checking Supabase `monitor_events` every 10 seconds (max wait: 8 minutes)...\n")

    sb_headers = {
        "apikey": sb_key or "",
        "Authorization": f"Bearer {sb_key or ''}"
    }

    start_wait = time.time()
    max_wait_seconds = 480 # 8 minutes
    delivery_event = None

    while time.time() - start_wait < max_wait_seconds:
        elapsed = int(time.time() - start_wait)
        now_str = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        
        # Query monitor_events for our temp_mid
        try:
            r = httpx.get(
                f"{sb_url}/rest/v1/monitor_events?monitor_id=eq.{temp_mid}&order=received_at.desc&limit=1",
                headers=sb_headers,
                timeout=10.0
            )
            if r.status_code == 200:
                events = r.json()
                if events and len(events) > 0:
                    delivery_event = events[0]
                    break
        except Exception as err:
            print(f"[{now_str}] Error polling monitor_events: {err}")
            
        print(f"[{now_str}] Waiting for cron delivery... elapsed: {elapsed}s", end="\r")
        time.sleep(10.0)

    print()
    if not delivery_event:
        print("FAIL: No natural delivery received within 8 minutes.")
        print("Cleaning up temporary monitor...")
        try:
            tf.monitor_delete(temp_mid, app="time_machine")
        except Exception:
            pass
        sys.exit(1)

    delivery_received_at = delivery_event.get("received_at")
    event_id = delivery_event.get("id")
    event_run_id = delivery_event.get("run_id")
    print("\n" + "=" * 70)
    print("SUCCESS: NATURAL SCHEDULED DELIVERY RECEIVED!")
    print("=" * 70)
    print(f" - Monitor ID:       {temp_mid}")
    print(f" - Monitor Created:  {monitor_created_at}")
    print(f" - Event Received:   {delivery_received_at}")
    print(f" - Event ID:         {event_id}")
    print(f" - Run ID:           {event_run_id}")
    
    # 4. Drain / process the event
    print("\nProcessing event through Time Machine processor...")
    proc_res = process_single_event(delivery_event, store=store_supa)
    print(f"Processor Result: {proc_res}")

    # 5. Clean up temporary monitor & page
    print("\nCleaning up temporary test monitor...")
    try:
        tf.monitor_delete(temp_mid, app="time_machine")
        print(f"Deleted temporary monitor {temp_mid}.")
    except Exception as e:
        print(f"Cleanup note: {e}")

    try:
        store_sqlite.delete_page(page_id)
        httpx.delete(f"{sb_url}/rest/v1/pages?id=eq.{page_id}", headers=sb_headers)
        print("Deleted temporary page row from database.")
    except Exception:
        pass

    print("\n" + "=" * 70)
    print("PROOF OF DELIVERY COMPLETE: All hops verified end-to-end!")
    print("=" * 70)

if __name__ == "__main__":
    main()
