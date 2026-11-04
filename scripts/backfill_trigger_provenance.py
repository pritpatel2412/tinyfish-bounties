"""
scripts/backfill_trigger_provenance.py
C1: Backfills trigger provenance on all existing checks in both SQLite and Supabase.
Rules:
- outcome == 'baseline' -> trigger = 'baseline'
- 2026-11-04 11:01:53 - 11:05:00 UTC manual trigger round -> trigger = 'manual'
- Early Acme test run -> trigger = 'manual'
- Webhook scheduled runs -> trigger = 'scheduled'
"""

import os
import json
import sqlite3
import httpx
from dotenv import dotenv_values

def backfill_sqlite(db_path: str = "data/app.db"):
    if not os.path.exists(db_path):
        print(f"[SQLITE] No database at {db_path}")
        return {}

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # 1. Ensure column exists
        try:
            conn.execute("ALTER TABLE checks ADD COLUMN trigger TEXT NOT NULL DEFAULT 'scheduled';")
            conn.commit()
            print("[SQLITE] Added 'trigger' column to checks table.")
        except Exception:
            pass  # Already exists

        cur = conn.execute("SELECT id, observed_at, outcome, detail FROM checks")
        rows = cur.fetchall()
        print(f"[SQLITE] Found {len(rows)} checks.")

        counts = {"baseline": 0, "manual": 0, "scheduled": 0}

        with conn:
            for r in rows:
                chk_id = r["id"]
                obs = r["observed_at"]
                outcome = r["outcome"]
                try:
                    detail = json.loads(r["detail"] or "{}")
                except Exception:
                    detail = {}

                if outcome == "baseline":
                    trig = "baseline"
                elif "2026-11-04T11:01:" <= obs <= "2026-11-04T11:06:":
                    trig = "manual"
                elif obs < "2026-11-04T04:00:00" and outcome != "baseline":
                    trig = "manual"
                else:
                    trig = "scheduled"

                detail["trigger"] = trig
                counts[trig] += 1

                conn.execute(
                    "UPDATE checks SET trigger = ?, detail = ? WHERE id = ?",
                    (trig, json.dumps(detail), chk_id)
                )

        print(f"[SQLITE] Backfill complete: {counts}")
        return counts
    finally:
        conn.close()


def backfill_supabase():
    cfg = dotenv_values(".env")
    sb_url = cfg.get("SUPABASE_URL")
    sb_key = cfg.get("SUPABASE_KEY")
    if not (sb_url and sb_key):
        print("[SUPABASE] Missing credentials in .env")
        return {}

    headers = {
        "apikey": sb_key,
        "Authorization": f"Bearer {sb_key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }

    with httpx.Client(timeout=20.0) as client:
        url = f"{sb_url}/rest/v1/checks?limit=500"
        resp = client.get(url, headers=headers)
        if resp.status_code != 200:
            print(f"[SUPABASE] Error fetching checks: {resp.status_code} {resp.text}")
            return {}

        checks = resp.json()
        print(f"[SUPABASE] Found {len(checks)} checks.")
        counts = {"baseline": 0, "manual": 0, "scheduled": 0}

        has_col = True
        for c in checks:
            chk_id = c["id"]
            obs = c.get("observed_at", "")
            outcome = c.get("outcome", "")
            detail = c.get("detail") or {}
            if isinstance(detail, str):
                try:
                    detail = json.loads(detail)
                except Exception:
                    detail = {}

            if outcome == "baseline":
                trig = "baseline"
            elif "2026-11-04T11:01:" <= obs <= "2026-11-04T11:06:":
                trig = "manual"
            elif obs < "2026-11-04T04:00:00" and outcome != "baseline":
                trig = "manual"
            else:
                trig = "scheduled"

            detail["trigger"] = trig
            counts[trig] += 1

            patch_data = {"detail": detail}
            if has_col:
                patch_data["trigger"] = trig

            patch_url = f"{sb_url}/rest/v1/checks?id=eq.{chk_id}"
            pr = client.patch(patch_url, headers=headers, json=patch_data)
            if pr.status_code not in (200, 204):
                if "trigger" in pr.text and ("schema cache" in pr.text or "does not exist" in pr.text):
                    has_col = False
                    # Retry without top-level column
                    pr2 = client.patch(patch_url, headers=headers, json={"detail": detail})
                    if pr2.status_code not in (200, 204):
                        print(f"[SUPABASE] Warning: fallback patch failed for {chk_id}: {pr2.status_code} {pr2.text}")
                else:
                    print(f"[SUPABASE] Warning: patch failed for {chk_id}: {pr2.status_code} {pr2.text}")

        print(f"[SUPABASE] Backfill complete (has_col={has_col}): {counts}")
        return counts


if __name__ == "__main__":
    print("=" * 60)
    print("C1: BACKFILL TRIGGER PROVENANCE")
    print("=" * 60)
    backfill_sqlite()
    backfill_supabase()
