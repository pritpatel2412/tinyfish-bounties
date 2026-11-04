"""
scripts/seed.py
Seeds vetted pages from data/pages_vetted.json into Time Machine:
- Idempotent: Skips pages already present in the store
- Spaced with 3s delays between creations
- Prints running monitor count, wallet balance, and summary
"""

import os
import sys

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import time
import json
from dotenv import dotenv_values

from tinycore.tf_client import TF
from tinycore.store import get_store
from apps.timemachine.capture import add_page


def main():
    vetted_file = os.path.join("data", "pages_vetted.json")
    if not os.path.exists(vetted_file):
        print(f"ERROR: {vetted_file} does not exist. Run scripts/vet_pages.py first.")
        sys.exit(1)

    with open(vetted_file, "r", encoding="utf-8") as f:
        pages = json.load(f)

    cfg = dotenv_values(".env")
    api_key = cfg.get("TINYFISH_API_KEY")
    if not api_key:
        print("ERROR: TINYFISH_API_KEY not found in .env")
        sys.exit(1)

    tf = TF(api_key=api_key)
    store = get_store()

    # Initial status
    wallet_info = tf.wallet_get()
    balance = wallet_info.get("available_balance") or wallet_info.get("balance")
    print(f"=== Seeding Time Machine Pages (Wallet Balance: ${balance}) ===")

    monitors_initial = tf.monitors_list()
    active_count = len([m for m in monitors_initial if str(m.get("status", "")).upper() != "PAUSED"])
    print(f"Current active monitors on account: {active_count}/50")
    print(f"Pages to seed: {len(pages)}\n")

    seeded_count = 0
    skipped_count = 0

    for i, p in enumerate(pages):
        url = p["url"]
        title = p["title"]
        category = p["category"]
        cron = p.get("schedule_cron")
        interval_label = p.get("interval_label", "6h")

        print(f"[{i+1}/{len(pages)}] Seeding {title} ({url}) ...")
        existing = store.get_page_by_url(url)
        if existing:
            print(f"   -> [SKIP] Already seeded with ID: {existing['id']}")
            skipped_count += 1
            continue

        try:
            res = add_page(
                url=url,
                title=title,
                category=category,
                cron=cron,
                interval_label=interval_label,
                store=store,
                tf=tf
            )
            seeded_count += 1
            page_id = res["page"]["id"]
            v1 = res["version"]
            shot_str = v1.get("screenshot_url") or "no-screenshot"
            print(f"   -> [OK] Page ID: {page_id} | Mon ID: {res['page'].get('monitor_id')}")
            print(f"   -> Baseline V1: observed_at={v1['observed_at']}, blocks={len(v1.get('blocks', []))}, shot={shot_str[:60]}")
        except Exception as e:
            print(f"   -> [ERROR] Failed to seed {url}: {e}")

        # Live count check
        try:
            cur_mons = tf.monitors_list()
            active_now = len([m for m in cur_mons if str(m.get("status", "")).upper() != "PAUSED"])
            print(f"   -> Live account active monitors: {active_now}/50")
        except Exception:
            pass

        # 3s spacing between page additions
        time.sleep(3)

    # Final summary
    print("\n" + "=" * 60)
    print("SEEDING COMPLETE")
    print("=" * 60)
    print(f"Successfully seeded: {seeded_count}")
    print(f"Skipped (already existing): {skipped_count}")
    print(f"Total pages in DB: {len(store.list_pages())}")


if __name__ == "__main__":
    main()
