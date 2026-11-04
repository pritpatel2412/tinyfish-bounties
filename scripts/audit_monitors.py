"""
scripts/audit_monitors.py
Audit and cleanup stray TinyFish monitors:
1. Lists all monitors from GET /v1/monitors
2. Compares to monitor_ids tracked in DB pages table
3. Prints table: monitor_id | name | url | cron | in_pages? | webhook_url
4. Deletes any monitor NOT present in pages table
5. Verifies live monitor count == tracked pages count
"""

import os
import sys

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import dotenv_values
from tinycore.tf_client import TF
from tinycore.store import get_store


def main():
    cfg = dotenv_values(".env")
    api_key = cfg.get("TINYFISH_API_KEY")
    if not api_key:
        print("ERROR: TINYFISH_API_KEY missing from .env")
        sys.exit(1)

    tf = TF(api_key=api_key)
    store = get_store()

    pages = store.list_pages()
    page_mon_map = {p["monitor_id"]: p for p in pages if p.get("monitor_id")}
    print(f"Loaded {len(pages)} pages from database ({len(page_mon_map)} with monitor_id).")

    print("\nFetching live monitors from TinyFish API...")
    live_monitors = tf.monitors_list()
    print(f"Total live monitors on TinyFish account: {len(live_monitors)}\n")

    # Header
    col_mid = 38
    col_name = 32
    col_url = 42
    col_cron = 14
    col_in = 10
    col_hook = 36

    header = (
        f"{'MONITOR ID':<{col_mid}} | "
        f"{'NAME':<{col_name}} | "
        f"{'URL':<{col_url}} | "
        f"{'CRON':<{col_cron}} | "
        f"{'IN PAGES?':<{col_in}} | "
        f"{'WEBHOOK URL':<{col_hook}}"
    )
    separator = "-" * len(header)

    print(separator)
    print(header)
    print(separator)

    to_delete = []

    for m in live_monitors:
        m_id = str(m.get("id", ""))
        name = str(m.get("name", ""))[:col_name]
        cfg_obj = m.get("config", {})
        url = str(cfg_obj.get("url") or "")[:col_url]
        cron = str(m.get("schedule_cron") or "")[:col_cron]
        in_pages = m_id in page_mon_map
        in_str = "YES" if in_pages else "NO (STRAY)"
        hook = str(m.get("webhook_url") or "")[:col_hook]

        print(
            f"{m_id:<{col_mid}} | "
            f"{name:<{col_name}} | "
            f"{url:<{col_url}} | "
            f"{cron:<{col_cron}} | "
            f"{in_str:<{col_in}} | "
            f"{hook:<{col_hook}}"
        )

        if not in_pages:
            to_delete.append((m_id, name, url))

    print(separator)

    # Cleanup phase
    if not to_delete:
        print("\nAll live monitors are tracked in pages table. No stray monitors to delete.")
    else:
        print(f"\nFound {len(to_delete)} stray monitor(s) to delete:")
        for mid, mname, murl in to_delete:
            print(f"  • Deleting stray monitor {mid} ({mname} | {murl})...")
            try:
                tf.monitor_delete(mid)
                print(f"    -> Deleted {mid} successfully.")
            except Exception as e:
                print(f"    -> Failed to delete {mid}: {e}")

    # Verification
    final_mons = tf.monitors_list()
    print(f"\nFinal count of active monitors on account: {len(final_mons)}")
    print(f"Target count (tracked pages in DB): {len(page_mon_map)}")

    if len(final_mons) == len(page_mon_map):
        print("PASS: Live monitor count exactly matches tracked pages in database.")
    else:
        print(f"WARNING: Discrepancy remaining: {len(final_mons)} live vs {len(page_mon_map)} tracked.")


if __name__ == "__main__":
    main()

