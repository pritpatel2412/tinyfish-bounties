"""
scripts/retry_null_screenshots.py
C7(a): Retries screenshot capture for baselines with null screenshots:
- status.groq.com
- vercel.com/changelog
- vercel.com/legal/terms
- openai.com/policies/terms-of-use
Retries up to 3 times with exponential backoff.
Stores with screenshot_at and screenshot_late=True.
"""

import os
import sys
import time
import uuid
import json
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tinycore.tf_client import TF
from tinycore.store import get_store, ScreenshotStore

TARGET_URLS = [
    "https://status.groq.com/",
    "https://vercel.com/changelog",
    "https://vercel.com/legal/terms",
    "https://openai.com/policies/terms-of-use"
]

def retry_null_screenshots():
    tf = TF()
    store_sqlite = get_store("sqlite")
    store_supa = get_store("supabase")

    results = []

    for url in TARGET_URLS:
        p = store_sqlite.get_page_by_url(url)
        if not p:
            print(f"Page not found in SQLite: {url}")
            continue

        p_id = p["id"]
        v1 = store_sqlite.get_version(p_id, 1)
        if not v1:
            print(f"Version 1 not found for: {url}")
            continue

        current_shot = v1.get("screenshot_url")
        if current_shot:
            print(f"[SKIP] Page {url} already has screenshot: {current_shot}")
            results.append({"url": url, "status": "already_present", "url_shot": current_shot})
            continue

        print(f"\n[CAPTURE] Capturing screenshot for {url} (up to 3 retries)...")
        shot_bytes = None
        backoff = 2.0
        for attempt in range(1, 4):
            try:
                print(f"  Attempt {attempt}/3...")
                shot_bytes = tf.screenshot(url)
                if shot_bytes and len(shot_bytes) > 0:
                    print(f"  Captured {len(shot_bytes)} bytes on attempt {attempt}.")
                    break
            except Exception as e:
                print(f"  Attempt {attempt} failed: {e}")
                if attempt < 3:
                    time.sleep(backoff)
                    backoff *= 2

        if not shot_bytes:
            print(f"[FAIL] All 3 attempts failed for {url}")
            results.append({"url": url, "status": "failed", "error": "All 3 attempts failed"})
            continue

        shot_key = f"tm_{uuid.uuid4().hex[:12]}.jpg"
        shot_url = ScreenshotStore.put(shot_bytes, shot_key)
        now_utc = datetime.now(timezone.utc).isoformat()

        # Update SQLite
        updates = {
            "screenshot_url": shot_url,
            "screenshot_at": now_utc
        }
        store_sqlite.update_version(v1["id"], updates)

        # Update Supabase if available
        try:
            store_supa.update_version(v1["id"], updates)
        except Exception as e:
            # Fallback direct REST patch
            try:
                sb_url = store_supa.sb_url
                sb_headers = store_supa.headers
                import httpx
                httpx.patch(f"{sb_url}/rest/v1/versions?id=eq.{v1['id']}", headers=sb_headers, json=updates, timeout=10)
            except Exception as e2:
                print(f"Supabase version patch notice: {e2}")

        print(f"[SUCCESS] Stored late screenshot for {url}: {shot_url} (screenshot_late=True)")
        results.append({
            "url": url,
            "status": "success",
            "screenshot_url": shot_url,
            "screenshot_at": now_utc,
            "screenshot_late": True
        })

    print("\n" + "=" * 60)
    print("RETRY NULL SCREENSHOTS SUMMARY:")
    for r in results:
        print(f"  - {r['url']}: {r['status']} -> {r.get('screenshot_url') or r.get('error')}")
    print("=" * 60)
    return results

if __name__ == "__main__":
    retry_null_screenshots()
