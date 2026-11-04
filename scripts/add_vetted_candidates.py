"""
scripts/add_vetted_candidates.py
Adds 4 vetted high-churn pages to Time Machine to expand coverage from 24 to 28 monitors:
- Captures initial v1 baseline + Browser CDP screenshot
- Staggers cron schedules
- Verifies live monitor count reaches 28
"""

import os
import sys
import time
import json
from dotenv import dotenv_values, load_dotenv

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tinycore.tf_client import TF
from tinycore.store import get_store
from apps.timemachine.capture import add_page

load_dotenv()

TARGETS = [
    {
        "url": "https://docs.python.org/3/whatsnew/",
        "title": "What's New in Python - Python Docs",
        "category": "changelog",
        "cron": "12 */2 * * *",
        "interval_label": "2h"
    },
    {
        "url": "https://developers.cloudflare.com/workers/platform/changelog/",
        "title": "Cloudflare Workers Changelog",
        "category": "changelog",
        "cron": "15 */2 * * *",
        "interval_label": "2h"
    },
    {
        "url": "https://nodejs.org/en/about/previous-releases",
        "title": "Node.js Releases",
        "category": "changelog",
        "cron": "18 */2 * * *",
        "interval_label": "2h"
    },
    {
        "url": "https://supabase.com/changelog",
        "title": "Supabase Changelog",
        "category": "changelog",
        "cron": "22 */2 * * *",
        "interval_label": "2h"
    }
]


def main():
    store = get_store()
    api_key = os.getenv("TINYFISH_API_KEY")
    tf = TF(api_key=api_key)

    print("Adding 4 vetted high-churn pages to reach 28/30 monitors...")
    for t in TARGETS:
        print(f"\nAdding: {t['title']} ({t['url']})")
        res = add_page(
            url=t["url"],
            title=t["title"],
            category=t["category"],
            cron=t["cron"],
            interval_label=t["interval_label"],
            store=store,
            tf=tf
        )
        p = res["page"]
        v1 = res["version"]
        print(f"  [OK] Page ID: {p['id']}, Monitor ID: {p.get('monitor_id')}")
        print(f"  [OK] Version 1: {v1['version_no']}, Blocks: {len(v1.get('blocks', []))}, Shot: {v1.get('screenshot_url')}")
        time.sleep(3.0)

    # Verify counts
    all_pages = [p for p in store.list_pages() if not p.get("is_test")]
    live_monitors = tf.monitors_list()
    active_monitors = [m for m in live_monitors if m.get("status") == "active"]

    print("\n================ COVERAGE EXPANSION SUMMARY ================")
    print(f"Total non-test pages in DB:     {len(all_pages)} / 30 target")
    print(f"Active TinyFish monitors:       {len(active_monitors)} / 50 cap")
    print(f"Time Machine quota utilization: {len(all_pages)} / 30 ({30 - len(all_pages)} spare)")
    print("============================================================\n")


if __name__ == "__main__":
    main()
