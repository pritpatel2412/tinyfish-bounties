"""
scripts/verify_scheduled_flow.py
Verifies scheduled monitoring cadence and flow for all non-test pages in Time Machine:
- Queries all 24 non-test pages in the database
- Inspects latest check outcome and timestamp
- Computes minutes_since_expected_next
- Flags any page where (now - last_check) > 1.5 * interval or monitor is paused
- Supports --trigger-due to run scheduled checks for overdue monitors before verification
- Exits non-zero if any page is flagged
"""

import os
import sys
import time
import argparse
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

# Ensure repo root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tinycore.store import get_store
from tinycore.tf_client import TF
from apps.timemachine.processor import insert_incoming_event, process_pending_events

load_dotenv()


def parse_interval_minutes(page: dict) -> int:
    """Parses interval in minutes from interval_label or schedule_cron."""
    label = (page.get("interval_label") or "").strip().lower()
    if label == "2h":
        return 120
    if label == "6h":
        return 360
    if label == "12h":
        return 720
    if label == "24h":
        return 1440

    cron = page.get("schedule_cron") or ""
    if "*/2" in cron:
        return 120
    if "*/6" in cron:
        return 360
    if "*/12" in cron:
        return 720
    if "*/24" in cron:
        return 1440
    return 360  # Default 6h


def trigger_due_checks(store, tf, pages, now_utc):
    """Triggers monitor runs for pages overdue for a scheduled check."""
    print("Checking for due monitors and triggering scheduled runs...\n")
    triggered = 0
    for p in pages:
        p_id = p["id"]
        interval_min = parse_interval_minutes(p)
        checks = store.get_checks(page_id=p_id, limit=1)
        last_dt = None
        if checks:
            try:
                last_dt = datetime.fromisoformat(checks[0]["observed_at"].replace("Z", "+00:00"))
            except Exception:
                pass
        if not last_dt and p.get("last_checked_at"):
            try:
                last_dt = datetime.fromisoformat(p["last_checked_at"].replace("Z", "+00:00"))
            except Exception:
                pass

        is_due = False
        if not last_dt:
            is_due = True
        else:
            elapsed_min = (now_utc - last_dt).total_seconds() / 60
            if elapsed_min >= interval_min:
                is_due = True

        mon_id = p.get("monitor_id")
        if is_due and mon_id:
            print(f"  [RUN] Triggering due monitor for: {p['url'][:55]} ({p.get('interval_label', '6h')})")
            try:
                run_data = tf.monitor_run(mon_id)
                run_id = run_data.get("id") or run_data.get("run_id")
                insert_incoming_event({
                    "monitor_id": mon_id,
                    "run_id": run_id,
                    "payload": run_data
                }, store=store)
                triggered += 1
                time.sleep(1.0)  # gentle pacing
            except Exception as e:
                print(f"    Error triggering run for {mon_id}: {e}")

    if triggered > 0:
        print(f"\nProcessing {triggered} triggered check events...")
        processed = process_pending_events(store=store, tf=tf)
        print(f"Processed {processed} events successfully.\n")


def verify_flow(trigger_due: bool = False) -> int:
    store = get_store()
    now_utc = datetime.now(timezone.utc)

    # 1. Fetch non-test pages
    pages = [p for p in store.list_pages() if not p.get("is_test")]
    print(f"Loaded {len(pages)} tracked non-test pages from database.")
    print(f"Verification Timestamp: {now_utc.isoformat()}\n")

    api_key = os.getenv("TINYFISH_API_KEY")
    tf = TF(api_key=api_key) if api_key else None

    if trigger_due and tf:
        trigger_due_checks(store, tf, pages, now_utc)
        # Refresh current time
        now_utc = datetime.now(timezone.utc)

    print("=" * 125)
    print(f"{'Page URL / Title':<42} | {'Int':<5} | {'Last Scheduled Check At':<27} | {'Outcome':<10} | {'Mins to Next / Overdue':<22} | {'Status'}")
    print("-" * 125)

    flagged_pages = []

    for p in pages:
        p_id = p["id"]
        interval_min = parse_interval_minutes(p)
        label = p.get("interval_label") or f"{interval_min // 60}h"

        checks = store.get_checks(page_id=p_id, limit=1)
        if checks:
            latest_chk = checks[0]
            last_check_iso = latest_chk["observed_at"]
            outcome = latest_chk["outcome"]
        else:
            last_check_iso = p.get("last_checked_at") or p.get("added_at") or "None"
            outcome = "none"

        last_dt = None
        if last_check_iso != "None":
            try:
                last_dt = datetime.fromisoformat(last_check_iso.replace("Z", "+00:00"))
            except Exception:
                pass

        if last_dt:
            elapsed_min = (now_utc - last_dt).total_seconds() / 60
            expected_next_dt = last_dt + timedelta(minutes=interval_min)
            mins_to_next = round((now_utc - expected_next_dt).total_seconds() / 60, 1)
            
            # Formatting mins_to_next column
            if mins_to_next < 0:
                mins_str = f"In {abs(mins_to_next):.1f} min"
            else:
                mins_str = f"+{mins_to_next:.1f} min overdue"

            is_overdue_threshold = elapsed_min > (1.5 * interval_min)
        else:
            elapsed_min = 999999
            mins_str = "No check recorded"
            is_overdue_threshold = True

        is_paused = (not p.get("active")) or (p.get("status") == "paused")
        is_flagged = is_overdue_threshold or is_paused

        if is_flagged:
            reasons = []
            if is_overdue_threshold:
                reasons.append(f">1.5x interval ({elapsed_min:.0f}m > {1.5 * interval_min:.0f}m)")
            if is_paused:
                reasons.append("monitor_paused")
            flag_status = f"[FLAGGED: {', '.join(reasons)}]"
            flagged_pages.append((p, reasons))
        else:
            flag_status = "[OK]"

        # Truncate title or url for tidy tabular output
        display_name = p.get("title") or p["url"]
        if len(display_name) > 40:
            display_name = display_name[:37] + "..."

        print(f"{display_name:<42} | {label:<5} | {last_check_iso:<27} | {outcome:<10} | {mins_str:<22} | {flag_status}")

    print("=" * 125)
    print(f"\nSummary: {len(pages)} total pages | {len(pages) - len(flagged_pages)} on cadence | {len(flagged_pages)} flagged")

    if flagged_pages:
        print("\n[!] The following pages were flagged for exceeding cadence threshold (1.5x interval) or being paused:")
        for fp, r in flagged_pages:
            print(f"  - {fp['url']}: {', '.join(r)}")
        return 1

    print("\n[OK] All pages are verified within scheduled monitoring cadence. Zero drift detected.")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Verify scheduled monitoring flow for Time Machine.")
    parser.add_argument("--trigger-due", action="store_true", help="Trigger scheduled check for overdue pages before verification")
    args = parser.parse_args()

    exit_code = verify_flow(trigger_due=args.trigger_due)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
