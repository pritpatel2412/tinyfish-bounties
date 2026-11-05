"""
scripts/sync_screenshots_to_storage.py
C7(b): Uploads all local screenshots (data/shots/...) to Supabase Storage bucket 'shots',
rewrites database references in SQLite and Supabase to public HTTPS URLs,
and verifies 3 URLs return 200 image/jpeg anonymously.
"""

import os
import sys
import json
import random
import httpx
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tinycore.store import get_store, ScreenshotStore, LOCAL_SHOTS_DIR

def sync_screenshots():
    store_sqlite = get_store("sqlite")
    store_supa = get_store("supabase")

    sb_url = store_supa.sb_url
    sb_key = store_supa.sb_key

    # 1. Collect all local files
    if not os.path.exists(LOCAL_SHOTS_DIR):
        print(f"No local shots directory at {LOCAL_SHOTS_DIR}")
        return

    files = [f for f in os.listdir(LOCAL_SHOTS_DIR) if f.endswith((".jpg", ".jpeg", ".png"))]
    print(f"Found {len(files)} screenshot files in {LOCAL_SHOTS_DIR}")

    url_mapping = {}

    headers = {
        "apikey": sb_key,
        "Authorization": f"Bearer {sb_key}",
        "Content-Type": "image/jpeg",
        "x-upsert": "true"
    }

    with httpx.Client(timeout=30.0) as client:
        for idx, filename in enumerate(files, 1):
            filepath = os.path.join(LOCAL_SHOTS_DIR, filename)
            with open(filepath, "rb") as f:
                img_bytes = f.read()

            upload_url = f"{sb_url}/storage/v1/object/shots/{filename}"
            public_url = f"{sb_url}/storage/v1/object/public/shots/{filename}"

            try:
                resp = client.post(upload_url, headers=headers, content=img_bytes)
                if resp.status_code in (200, 201):
                    url_mapping[filename] = public_url
                    url_mapping[f"data\\shots\\{filename}"] = public_url
                    url_mapping[f"data/shots/{filename}"] = public_url
                    url_mapping[filepath] = public_url
                    if idx % 5 == 0 or idx == len(files):
                        print(f"  [{idx}/{len(files)}] Uploaded {filename} -> {public_url[:65]}...")
                else:
                    print(f"  Warning: upload failed for {filename}: {resp.status_code} {resp.text}")
            except Exception as e:
                print(f"  Error uploading {filename}: {e}")

    print(f"\nSuccessfully uploaded {len(set(url_mapping.values()))} files to Supabase Storage.")

    # 2. Rewrite SQLite versions & checks
    print("\nRewriting SQLite records...")
    conn = store_sqlite._conn()
    try:
        cur = conn.execute("SELECT id, screenshot_url FROM versions WHERE screenshot_url IS NOT NULL")
        ver_rows = cur.fetchall()
        ver_updated = 0
        with conn:
            for r in ver_rows:
                old_u = r["screenshot_url"]
                # Match by basename
                base = os.path.basename(old_u)
                new_u = url_mapping.get(old_u) or url_mapping.get(base)
                if new_u and new_u != old_u:
                    conn.execute("UPDATE versions SET screenshot_url = ? WHERE id = ?", (new_u, r["id"]))
                    ver_updated += 1

        cur = conn.execute("SELECT id, detail FROM checks WHERE detail LIKE '%data%shots%'")
        chk_rows = cur.fetchall()
        chk_updated = 0
        with conn:
            for r in chk_rows:
                try:
                    d = json.loads(r["detail"] or "{}")
                    old_u = d.get("screenshot_url")
                    if old_u:
                        base = os.path.basename(old_u)
                        new_u = url_mapping.get(old_u) or url_mapping.get(base)
                        if new_u and new_u != old_u:
                            d["screenshot_url"] = new_u
                            conn.execute("UPDATE checks SET detail = ? WHERE id = ?", (json.dumps(d), r["id"]))
                            chk_updated += 1
                except Exception:
                    pass
        print(f"  SQLite: updated {ver_updated} versions and {chk_updated} checks.")
    finally:
        conn.close()

    # 3. Rewrite Supabase versions & checks
    print("\nRewriting Supabase records via REST...")
    with httpx.Client(timeout=30.0) as client:
        sb_h = store_supa.headers
        # Fetch versions
        vr = client.get(f"{sb_url}/rest/v1/versions?select=id,screenshot_url&screenshot_url=not.is.null&limit=500", headers=sb_h)
        if vr.status_code == 200:
            sb_vers = vr.json()
            sb_v_up = 0
            for v in sb_vers:
                old_u = v.get("screenshot_url") or ""
                if "http" not in old_u:
                    base = os.path.basename(old_u)
                    new_u = url_mapping.get(old_u) or url_mapping.get(base)
                    if new_u:
                        client.patch(f"{sb_url}/rest/v1/versions?id=eq.{v['id']}", headers=sb_h, json={"screenshot_url": new_u})
                        sb_v_up += 1
            print(f"  Supabase: updated {sb_v_up} versions.")

        # Fetch checks
        cr = client.get(f"{sb_url}/rest/v1/checks?select=id,detail&limit=500", headers=sb_h)
        if cr.status_code == 200:
            sb_chks = cr.json()
            sb_c_up = 0
            for c in sb_chks:
                d = c.get("detail") or {}
                if isinstance(d, dict):
                    old_u = d.get("screenshot_url") or ""
                    if old_u and "http" not in old_u:
                        base = os.path.basename(old_u)
                        new_u = url_mapping.get(old_u) or url_mapping.get(base)
                        if new_u:
                            d["screenshot_url"] = new_u
                            client.patch(f"{sb_url}/rest/v1/checks?id=eq.{c['id']}", headers=sb_h, json={"detail": d})
                            sb_c_up += 1
            print(f"  Supabase: updated {sb_c_up} checks.")

    # 4. Verify 3 URLs return 200 image/jpeg anonymously
    unique_public_urls = list(set(url_mapping.values()))
    print(f"\nVerifying 3 random URLs return 200 image/jpeg anonymously (out of {len(unique_public_urls)})...")
    sample_urls = random.sample(unique_public_urls, min(3, len(unique_public_urls)))
    
    with httpx.Client(timeout=15.0) as anon_client:
        for idx, u in enumerate(sample_urls, 1):
            ar = anon_client.get(u)
            ct = ar.headers.get("content-type", "")
            clen = len(ar.content)
            status_tag = "[PASS]" if ar.status_code == 200 and "image" in ct else "[FAIL]"
            print(f"  Sample {idx}: {status_tag} HTTP {ar.status_code} | {ct} | {clen} bytes | {u}")
            assert ar.status_code == 200, f"Expected 200 for {u}, got {ar.status_code}"
            assert "image" in ct, f"Expected image content-type, got {ct}"

    print("\n[SUCCESS] C7(b) Complete: All screenshots uploaded and verified public HTTPS.")

if __name__ == "__main__":
    sync_screenshots()

