"""
scripts/daily_check.py
Daily operational dashboard for Time Machine:
- Checks TinyFish account: wallet balance, active monitors quota
- Prints tracked pages status: active, category breakdown
- Aggregates last 24h checks: baseline, unchanged, noise, changed, error
- Lists real changes caught with summaries and screenshot links
- Checks unprocessed webhook events
- Inspects cost ledger and daily budget headroom
"""

import os
import sys

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from datetime import datetime, timezone, timedelta
from dotenv import dotenv_values

from tinycore.store import get_store
from tinycore.tf_client import TF
from tinycore.ledger import CostLedger, MonitorBudget
from tinycore.api_common import get_endpoint_usage_summary


def format_header(title: str):
    print("\n" + "=" * 60)
    print(f" {title.upper()}")
    print("=" * 60)


def main():
    cfg = dotenv_values(".env")
    api_key = cfg.get("TINYFISH_API_KEY")
    tf = TF(api_key=api_key) if api_key else None
    store = get_store()

    now_utc = datetime.now(timezone.utc)
    cutoff_24h = (now_utc - timedelta(hours=24)).isoformat()

    format_header(f"Time Machine Daily Check — {now_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}")

    # 1. TinyFish Account & Wallet Health
    print("\n[1] TINYFISH ACCOUNT & BUDGET")
    if tf:
        try:
            w_data = tf.wallet_get()
            balance = None
            if isinstance(w_data, dict):
                bal_str = w_data.get("available_balance")
                if bal_str is not None:
                    balance = float(bal_str)
            elif isinstance(w_data, (int, float)):
                balance = float(w_data)

            bal_display = f"${balance:.2f}" if balance is not None else "Unknown"
            status_flag = "OK" if balance and balance >= 8.00 else "LOW ALERT"
            print(f"  • Wallet Balance:       {bal_display} [{status_flag}]")
        except Exception as e:
            print(f"  • Wallet Balance:       Error ({e})")

        try:
            live_monitors = tf.monitors_list()
            active_m = [m for m in live_monitors if str(m.get("status", "")).upper() != "PAUSED"]
            paused_m = [m for m in live_monitors if str(m.get("status", "")).upper() == "PAUSED"]
            print(f"  • Live Monitors:        {len(live_monitors)} total ({len(active_m)} active, {len(paused_m)} paused)")
        except Exception as e:
            print(f"  • Live Monitors:        Error ({e})")
    else:
        print("  • TinyFish API Key:     Missing (.env)")

    # Ledger
    spend_today = CostLedger.get_daily_spend()
    max_daily = CostLedger.get_max_daily()
    headroom = max(0.0, max_daily - spend_today)
    print(f"  • Daily Spend Today:    ${spend_today:.4f} / ${max_daily:.2f} (Headroom: ${headroom:.4f})")

    # Monitor Budget Allocation
    alloc = MonitorBudget.get_status()
    print(f"  • Monitor Quota Status: {alloc.get('used', 0)}/{alloc.get('limit', 30)} allocated to Time Machine")

    # 2. Tracked Pages Overview
    print("\n[2] TRACKED PAGES OVERVIEW")
    pages = store.list_pages()
    active_pages = [p for p in pages if p.get("active")]
    inactive_pages = [p for p in pages if not p.get("active")]
    print(f"  • Total Tracked Pages:  {len(pages)} ({len(active_pages)} active, {len(inactive_pages)} inactive)")

    # Categories breakdown
    cats = {}
    for p in pages:
        c = p.get("category", "general")
        cats[c] = cats.get(c, 0) + 1
    cat_summary = ", ".join(f"{k}: {v}" for k, v in sorted(cats.items()))
    print(f"  • Category Breakdown:   {cat_summary}")

    # 3. Last 24 Hours Monitoring Cadence
    print(f"\n[3] CHECKS IN LAST 24 HOURS (since {cutoff_24h[:19]}Z)")
    all_checks = store.get_checks(limit=1000)
    recent_checks = [c for c in all_checks if c.get("observed_at", "") >= cutoff_24h]

    outcome_counts = {}
    for c in recent_checks:
        out = c.get("outcome", "unknown")
        outcome_counts[out] = outcome_counts.get(out, 0) + 1

    print(f"  • Total Checks (24h):   {len(recent_checks)}")
    for out, count in sorted(outcome_counts.items()):
        print(f"    - {out.upper():<12}: {count}")

    # 4. Changes Caught in Last 24h
    print("\n[4] MATERIAL CHANGES CAUGHT (24h)")
    changed_checks = [c for c in recent_checks if c.get("outcome") == "changed"]
    if not changed_checks:
        print("  • No material changes recorded in the last 24 hours.")
    else:
        for idx, chk in enumerate(changed_checks, 1):
            detail = chk.get("detail", {})
            classif = detail.get("classification") or {}
            page_id = chk.get("page_id")
            page_meta = next((p for p in pages if p["id"] == page_id), {})
            print(f"  [{idx}] {page_meta.get('title') or page_meta.get('url')} (v{detail.get('version_no')})")
            print(f"      Time:       {chk.get('observed_at')}")
            print(f"      Summary:    {classif.get('summary', 'Change detected')}")
            print(f"      Category:   {classif.get('category', 'unknown')}")
            if detail.get("screenshot_url"):
                print(f"      Screenshot: {detail.get('screenshot_url')}")

    # 5. Pending Webhook Events
    print("\n[5] UNPROCESSED WEBHOOK QUEUE")
    if hasattr(store, "_conn"):
        conn = store._conn()
        try:
            cur = conn.execute("SELECT count(*) as cnt FROM monitor_events WHERE processed = 0")
            unprocessed_cnt = cur.fetchone()["cnt"]
            print(f"  • Unprocessed Events:   {unprocessed_cnt}")
        finally:
            conn.close()
    else:
        print("  • Unprocessed Events:   Remote Supabase Store")

    # 6. TinyFish API Call Aggregates
    print("\n[6] TINYFISH CALL METRICS (Audit Log)")
    usage = get_endpoint_usage_summary()
    print(f"  • Total API Calls:      {usage.get('total_calls', 0)}")
    print(f"  • Total Incurred Cost:  ${usage.get('total_cost_usd', 0.0):.4f}")
    for ep, meta in usage.get("endpoints", {}).items():
        print(f"    - {ep:<20}: {meta['total_calls']} calls ({meta['success']} ok, {meta['failed']} fail) | Avg: {meta['avg_latency_ms']}ms | ${meta['total_cost_usd']:.4f}")

    print("\n" + "=" * 60)
    print(" DAILY CHECK COMPLETE")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
