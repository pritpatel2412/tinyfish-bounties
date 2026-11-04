"""
scripts/migrate_sqlite_to_supabase.py
Idempotent migration from SQLite (data/app.db) to Supabase:
- Copies pages, versions, diffs, checks (preserving observed_at, history_starts_at, monitor_id, is_test)
- Uploads local screenshots from data/shots/ to Supabase Storage bucket 'shots'
- Rewrites versions.screenshot_url to public HTTPS URLs
- Verifies 3 random screenshot URLs return HTTP 200 with image/jpeg anonymously
- Compares local vs remote counts
"""

import os
import sys
import json
import sqlite3
import random
import httpx
from dotenv import dotenv_values, load_dotenv

# Ensure repo root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tinycore.store import DB_PATH, LOCAL_SHOTS_DIR, get_db

load_dotenv()

SB_URL = os.getenv("SUPABASE_URL")
SB_KEY = os.getenv("SUPABASE_KEY")
BUCKET_NAME = "shots"


def get_supabase_headers():
    if not SB_URL or not SB_KEY:
        raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set in environment or .env")
    return {
        "apikey": SB_KEY,
        "Authorization": f"Bearer {SB_KEY}",
        "Content-Type": "application/json"
    }


def ensure_bucket_exists(client: httpx.Client) -> bool:
    """Ensures Supabase Storage bucket 'shots' exists and is public."""
    headers = get_supabase_headers()
    # Check if bucket exists
    check_url = f"{SB_URL}/storage/v1/bucket/{BUCKET_NAME}"
    resp = client.get(check_url, headers=headers)
    if resp.status_code == 200:
        print(f"[OK] Supabase Storage bucket '{BUCKET_NAME}' already exists.")
        return True

    # Try creating bucket
    create_url = f"{SB_URL}/storage/v1/bucket"
    payload = {"id": BUCKET_NAME, "name": BUCKET_NAME, "public": True}
    resp = client.post(create_url, headers=headers, json=payload)
    if resp.status_code in (200, 201):
        print(f"[OK] Created Supabase Storage bucket '{BUCKET_NAME}' (public).")
        return True
    else:
        print(f"[!] Could not create bucket '{BUCKET_NAME}' automatically ({resp.status_code}: {resp.text}).")
        print("  If RLS is enabled, create bucket 'shots' manually in Supabase Dashboard -> Storage.")
        return False


def upload_screenshots(client: httpx.Client) -> dict[str, str]:
    """Uploads local screenshots to Supabase Storage and returns a mapping from filename/local path to public URL."""
    headers = get_supabase_headers()
    url_map = {}

    if not os.path.exists(LOCAL_SHOTS_DIR):
        print(f"No local shots directory found at {LOCAL_SHOTS_DIR}.")
        return url_map

    files = [f for f in os.listdir(LOCAL_SHOTS_DIR) if f.lower().endswith((".jpg", ".jpeg", ".png"))]
    print(f"\n--- Uploading {len(files)} screenshots to Supabase Storage bucket '{BUCKET_NAME}' ---")

    upload_headers = {
        "apikey": SB_KEY,
        "Authorization": f"Bearer {SB_KEY}",
        "Content-Type": "image/jpeg",
        "x-upsert": "true"
    }

    uploaded = 0
    for filename in sorted(files):
        local_path = os.path.join(LOCAL_SHOTS_DIR, filename)
        with open(local_path, "rb") as f:
            image_bytes = f.read()

        upload_url = f"{SB_URL}/storage/v1/object/{BUCKET_NAME}/{filename}"
        public_url = f"{SB_URL}/storage/v1/object/public/{BUCKET_NAME}/{filename}"

        try:
            resp = client.post(upload_url, headers=upload_headers, content=image_bytes)
            if resp.status_code in (200, 201):
                url_map[filename] = public_url
                url_map[local_path] = public_url
                uploaded += 1
            else:
                print(f"  Failed to upload {filename}: {resp.status_code} {resp.text}")
                # Still map to public URL structure so database records get correct canonical URL
                url_map[filename] = public_url
                url_map[local_path] = public_url
        except Exception as e:
            print(f"  Error uploading {filename}: {e}")
            url_map[filename] = public_url
            url_map[local_path] = public_url

    print(f"[OK] Uploaded {uploaded}/{len(files)} screenshots to Supabase Storage.")
    return url_map


def verify_screenshot_urls(urls: list[str]):
    """Anonymously verifies 3 random URLs return HTTP 200 with image/jpeg."""
    if not urls:
        print("No screenshot URLs to verify.")
        return

    sample_size = min(3, len(urls))
    samples = random.sample(urls, sample_size)
    print(f"\n--- Verifying {sample_size} random screenshot URLs anonymously ---")

    with httpx.Client(timeout=10.0) as anon_client:
        for url in samples:
            try:
                # Anonymous GET without apikey/authorization headers
                resp = anon_client.get(url)
                content_type = resp.headers.get("content-type", "")
                status = resp.status_code
                is_ok = status == 200 and "image" in content_type
                symbol = "[OK]" if is_ok else "[X]"
                print(f"  {symbol} GET {url}")
                print(f"    Status: {status} | Content-Type: {content_type} | Size: {len(resp.content)} bytes")
            except Exception as e:
                print(f"  [X] GET {url} failed: {e}")


def migrate_tables(client: httpx.Client, screenshot_map: dict[str, str]):
    """Reads SQLite data and upserts into Supabase PostgREST tables."""
    conn = get_db(DB_PATH)
    headers = get_supabase_headers()
    headers["Prefer"] = "resolution=merge-duplicates,return=minimal"

    try:
        # 1. Pages
        cur = conn.execute("SELECT * FROM pages")
        sqlite_pages = [dict(r) for r in cur.fetchall()]
        print(f"\n--- Migrating {len(sqlite_pages)} Pages ---")
        
        # Convert types for JSON/Postgres
        formatted_pages = []
        for p in sqlite_pages:
            p_copy = dict(p)
            p_copy["active"] = bool(p_copy.get("active", 1))
            p_copy["is_test"] = bool(p_copy.get("is_test", 0))
            if isinstance(p_copy.get("ignore_selectors"), str):
                try:
                    p_copy["ignore_selectors"] = json.loads(p_copy["ignore_selectors"])
                except Exception:
                    p_copy["ignore_selectors"] = []
            formatted_pages.append(p_copy)

        pages_resp = client.post(
            f"{SB_URL}/rest/v1/pages?on_conflict=url",
            headers=headers,
            json=formatted_pages
        )
        if pages_resp.status_code in (200, 201, 204):
            print(f"[OK] Pages upserted successfully: {len(formatted_pages)} rows")
        else:
            print(f"[!] Pages upsert returned {pages_resp.status_code}: {pages_resp.text}")

        # 2. Versions
        cur = conn.execute("SELECT * FROM versions")
        sqlite_versions = [dict(r) for r in cur.fetchall()]
        print(f"\n--- Migrating {len(sqlite_versions)} Versions ---")
        
        formatted_versions = []
        all_public_shot_urls = []
        for v in sqlite_versions:
            v_copy = dict(v)
            if isinstance(v_copy.get("blocks"), str):
                try:
                    v_copy["blocks"] = json.loads(v_copy["blocks"])
                except Exception:
                    v_copy["blocks"] = []
            if isinstance(v_copy.get("classification"), str) and v_copy["classification"]:
                try:
                    v_copy["classification"] = json.loads(v_copy["classification"])
                except Exception:
                    v_copy["classification"] = None

            # Rewrite screenshot_url if it matches a local path or filename
            old_shot = v_copy.get("screenshot_url")
            if old_shot:
                filename = os.path.basename(old_shot)
                new_shot = screenshot_map.get(old_shot) or screenshot_map.get(filename) or f"{SB_URL}/storage/v1/object/public/{BUCKET_NAME}/{filename}"
                v_copy["screenshot_url"] = new_shot
                all_public_shot_urls.append(new_shot)

            formatted_versions.append(v_copy)

        if formatted_versions:
            ver_resp = client.post(
                f"{SB_URL}/rest/v1/versions?on_conflict=page_id,version_no",
                headers=headers,
                json=formatted_versions
            )
            if ver_resp.status_code in (200, 201, 204):
                print(f"[OK] Versions upserted successfully: {len(formatted_versions)} rows")
            else:
                print(f"[!] Versions upsert returned {ver_resp.status_code}: {ver_resp.text}")

        # 3. Diffs
        cur = conn.execute("SELECT * FROM diffs")
        sqlite_diffs = [dict(r) for r in cur.fetchall()]
        print(f"\n--- Migrating {len(sqlite_diffs)} Diffs ---")
        formatted_diffs = []
        for d in sqlite_diffs:
            d_copy = dict(d)
            if isinstance(d_copy.get("diff"), str):
                try:
                    d_copy["diff"] = json.loads(d_copy["diff"])
                except Exception:
                    d_copy["diff"] = {}
            formatted_diffs.append(d_copy)

        if formatted_diffs:
            diff_resp = client.post(
                f"{SB_URL}/rest/v1/diffs?on_conflict=id",
                headers=headers,
                json=formatted_diffs
            )
            if diff_resp.status_code in (200, 201, 204):
                print(f"[OK] Diffs upserted successfully: {len(formatted_diffs)} rows")
            else:
                print(f"[!] Diffs upsert returned {diff_resp.status_code}: {diff_resp.text}")

        # 4. Checks
        cur = conn.execute("SELECT * FROM checks")
        sqlite_checks = [dict(r) for r in cur.fetchall()]
        print(f"\n--- Migrating {len(sqlite_checks)} Checks ---")
        formatted_checks = []
        for c in sqlite_checks:
            c_copy = dict(c)
            if isinstance(c_copy.get("detail"), str):
                try:
                    c_copy["detail"] = json.loads(c_copy["detail"] or "{}")
                except Exception:
                    c_copy["detail"] = {}
            formatted_checks.append(c_copy)

        if formatted_checks:
            check_resp = client.post(
                f"{SB_URL}/rest/v1/checks?on_conflict=run_id",
                headers=headers,
                json=formatted_checks
            )
            if check_resp.status_code in (200, 201, 204):
                print(f"[OK] Checks upserted successfully: {len(formatted_checks)} rows")
            else:
                print(f"[!] Checks upsert returned {check_resp.status_code}: {check_resp.text}")

        return all_public_shot_urls

    finally:
        conn.close()


def compare_counts(client: httpx.Client):
    """Compares SQLite counts to Supabase counts."""
    headers = get_supabase_headers()
    conn = get_db(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM pages")
        local_pages = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM versions")
        local_versions = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM diffs")
        local_diffs = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM checks")
        local_checks = cur.fetchone()[0]

        print("\n================ STORAGE BACKEND AUDIT ================")
        print(f"{'Entity':<15} | {'SQLite (Local)':<15} | {'Supabase (Remote)':<15}")
        print("-" * 50)

        for name, local_cnt in [
            ("pages", local_pages),
            ("versions", local_versions),
            ("diffs", local_diffs),
            ("checks", local_checks)
        ]:
            resp = client.get(f"{SB_URL}/rest/v1/{name}?select=count", headers={**headers, "Range": "0-0"})
            if resp.status_code in (200, 206):
                # Header format: 0-0/25
                content_range = resp.headers.get("content-range", "")
                remote_cnt = content_range.split("/")[-1] if "/" in content_range else "0"
            else:
                remote_cnt = f"ERR ({resp.status_code})"
            print(f"{name:<15} | {local_cnt:<15} | {remote_cnt:<15}")

        print("=======================================================\n")
    finally:
        conn.close()


def main():
    print(f"Starting SQLite -> Supabase migration (Target: {SB_URL})")
    with httpx.Client(timeout=20.0) as client:
        ensure_bucket_exists(client)
        shot_map = upload_screenshots(client)
        all_shots = migrate_tables(client, shot_map)
        if all_shots:
            verify_screenshot_urls(all_shots)
        compare_counts(client)


if __name__ == "__main__":
    main()
