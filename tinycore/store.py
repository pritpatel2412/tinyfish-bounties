"""
tinycore/store.py
Storage abstraction supporting both SqliteStore (local/dev/testing) and SupabaseStore (prod/cloud).
Selected via STORE_BACKEND environment variable ('sqlite' or 'supabase').
Also provides ScreenshotStore managing uploads to Supabase Storage bucket 'shots' (with local fallback).
"""

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone

import httpx
from dotenv import dotenv_values

DB_PATH = os.path.join("data", "app.db")
LOCAL_SHOTS_DIR = os.path.join("data", "shots")
BUCKET_NAME = "shots"


def get_db(db_path: str = DB_PATH) -> sqlite3.Connection:
    """Returns a SQLite connection configured with WAL mode and row factory."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db(db_path: str = DB_PATH):
    """Initializes tables for Time Machine in SQLite."""
    conn = get_db(db_path)
    try:
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pages (
                    id TEXT PRIMARY KEY,
                    url TEXT UNIQUE NOT NULL,
                    title TEXT,
                    category TEXT,
                    monitor_id TEXT,
                    schedule_cron TEXT,
                    interval_label TEXT,
                    ignore_selectors TEXT DEFAULT '[]',
                    added_at TEXT NOT NULL,
                    history_starts_at TEXT,
                    active INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'active',
                    last_checked_at TEXT,
                    is_test INTEGER NOT NULL DEFAULT 0
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pages_url ON pages(url);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pages_monitor ON pages(monitor_id);")
            try:
                conn.execute("ALTER TABLE pages ADD COLUMN is_test INTEGER NOT NULL DEFAULT 0;")
            except Exception:
                pass

            conn.execute("""
                CREATE TABLE IF NOT EXISTS versions (
                    id TEXT PRIMARY KEY,
                    page_id TEXT NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
                    version_no INTEGER NOT NULL,
                    observed_at TEXT NOT NULL,
                    processed_at TEXT NOT NULL,
                    lag_seconds INTEGER,
                    content_hash TEXT NOT NULL,
                    blocks TEXT NOT NULL,
                    text_md TEXT,
                    screenshot_url TEXT,
                    screenshot_at TEXT,
                    source_url TEXT NOT NULL,
                    run_id TEXT,
                    classification TEXT,
                    summary TEXT,
                    review_status TEXT NOT NULL DEFAULT 'unreviewed',
                    review_note TEXT,
                    UNIQUE(page_id, version_no)
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ver_page_no ON versions(page_id, version_no);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ver_observed ON versions(page_id, observed_at);")
            try:
                conn.execute("ALTER TABLE versions ADD COLUMN review_status TEXT NOT NULL DEFAULT 'unreviewed';")
            except Exception:
                pass
            try:
                conn.execute("ALTER TABLE versions ADD COLUMN review_note TEXT;")
            except Exception:
                pass

            conn.execute("""
                CREATE TABLE IF NOT EXISTS diffs (
                    id TEXT PRIMARY KEY,
                    page_id TEXT NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
                    from_version INTEGER NOT NULL,
                    to_version INTEGER NOT NULL,
                    diff TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS checks (
                    id TEXT PRIMARY KEY,
                    page_id TEXT NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
                    observed_at TEXT NOT NULL,
                    run_id TEXT UNIQUE,
                    outcome TEXT NOT NULL,
                    detail TEXT,
                    trigger TEXT NOT NULL DEFAULT 'scheduled'
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_checks_page ON checks(page_id, observed_at);")
            try:
                conn.execute("ALTER TABLE checks ADD COLUMN trigger TEXT NOT NULL DEFAULT 'scheduled';")
            except Exception:
                pass

            conn.execute("""
                CREATE TABLE IF NOT EXISTS monitor_events (
                    id TEXT PRIMARY KEY,
                    received_at TEXT NOT NULL,
                    monitor_id TEXT,
                    run_id TEXT,
                    payload TEXT NOT NULL,
                    processed INTEGER NOT NULL DEFAULT 0
                );
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS suggestions (
                    id TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    title TEXT,
                    note TEXT,
                    suggested_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending_approval'
                );
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS evidence_receipts (
                    id TEXT PRIMARY KEY,
                    source_url TEXT NOT NULL,
                    checked_at TEXT NOT NULL,
                    html_sha256 TEXT NOT NULL,
                    screenshot_url TEXT,
                    tf_run_id TEXT,
                    endpoint_used TEXT NOT NULL
                );
            """)
    finally:
        conn.close()


class SqliteStore:
    """Local SQLite backend for Time Machine storage."""
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        init_db(self.db_path)

    def _conn(self) -> sqlite3.Connection:
        return get_db(self.db_path)

    def add_page(self, page: dict) -> dict:
        conn = self._conn()
        try:
            page_id = page.get("id") or str(uuid.uuid4())
            now_utc = datetime.now(timezone.utc).isoformat()
            added_at = page.get("added_at") or now_utc
            active = 1 if page.get("active", True) else 0
            ignore_sel = json.dumps(page.get("ignore_selectors", [])) if not isinstance(page.get("ignore_selectors"), str) else page.get("ignore_selectors")
            is_test_val = 1 if page.get("is_test") else 0
            with conn:
                conn.execute("""
                    INSERT INTO pages (id, url, title, category, monitor_id, schedule_cron, interval_label,
                                       ignore_selectors, added_at, history_starts_at, active, status, last_checked_at, is_test)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    page_id, page["url"], page.get("title"), page.get("category"), page.get("monitor_id"),
                    page.get("schedule_cron"), page.get("interval_label"), ignore_sel,
                    added_at, page.get("history_starts_at"), active, page.get("status", "active"), page.get("last_checked_at"),
                    is_test_val
                ))
            return self.get_page(page_id)  # type: ignore
        finally:
            conn.close()

    def get_page(self, page_id: str) -> dict | None:
        conn = self._conn()
        try:
            cur = conn.execute("SELECT * FROM pages WHERE id = ?", (page_id,))
            row = cur.fetchone()
            if not row:
                return None
            d = dict(row)
            d["active"] = bool(d["active"])
            d["is_test"] = bool(d.get("is_test", 0))
            try:
                d["ignore_selectors"] = json.loads(d["ignore_selectors"] or "[]")
            except Exception:
                d["ignore_selectors"] = []
            return d
        finally:
            conn.close()

    def get_page_by_url(self, url: str) -> dict | None:
        conn = self._conn()
        try:
            cur = conn.execute("SELECT * FROM pages WHERE url = ?", (url,))
            row = cur.fetchone()
            if not row:
                return None
            d = dict(row)
            d["active"] = bool(d["active"])
            d["is_test"] = bool(d.get("is_test", 0))
            try:
                d["ignore_selectors"] = json.loads(d["ignore_selectors"] or "[]")
            except Exception:
                d["ignore_selectors"] = []
            return d
        finally:
            conn.close()

    def list_pages(self, active_only: bool = False) -> list[dict]:
        conn = self._conn()
        try:
            q = "SELECT * FROM pages"
            params = ()
            if active_only:
                q += " WHERE active = 1"
            q += " ORDER BY added_at ASC"
            cur = conn.execute(q, params)
            results = []
            for row in cur.fetchall():
                d = dict(row)
                d["active"] = bool(d["active"])
                d["is_test"] = bool(d.get("is_test", 0))
                try:
                    d["ignore_selectors"] = json.loads(d["ignore_selectors"] or "[]")
                except Exception:
                    d["ignore_selectors"] = []
                results.append(d)
            return results
        finally:
            conn.close()

    def delete_page(self, page_id: str) -> bool:
        conn = self._conn()
        try:
            with conn:
                cur = conn.execute("DELETE FROM pages WHERE id = ?", (page_id,))
                return cur.rowcount > 0
        finally:
            conn.close()

    def update_page(self, page_id: str, updates: dict) -> dict | None:
        conn = self._conn()
        try:
            set_clauses = []
            params = []
            for k, v in updates.items():
                if k in ("id", "url"):
                    continue
                set_clauses.append(f"{k} = ?")
                if k in ("active", "is_test"):
                    params.append(1 if v else 0)
                elif k == "ignore_selectors" and not isinstance(v, str):
                    params.append(json.dumps(v))
                else:
                    params.append(v)
            if not set_clauses:
                return self.get_page(page_id)

            params.append(page_id)
            with conn:
                conn.execute(f"UPDATE pages SET {', '.join(set_clauses)} WHERE id = ?", tuple(params))
            return self.get_page(page_id)
        finally:
            conn.close()

    def add_version(self, v: dict) -> dict:
        conn = self._conn()
        try:
            vid = v.get("id") or str(uuid.uuid4())
            proc_at = v.get("processed_at") or datetime.now(timezone.utc).isoformat()
            blocks_raw = json.dumps(v.get("blocks", [])) if not isinstance(v.get("blocks"), str) else v.get("blocks")
            class_raw = json.dumps(v.get("classification")) if v.get("classification") is not None and not isinstance(v.get("classification"), str) else v.get("classification")
            with conn:
                conn.execute("""
                    INSERT INTO versions (id, page_id, version_no, observed_at, processed_at, lag_seconds,
                                         content_hash, blocks, text_md, screenshot_url, screenshot_at,
                                         source_url, run_id, classification, summary, review_status, review_note)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    vid, v["page_id"], v["version_no"], v["observed_at"], proc_at, v.get("lag_seconds"),
                    v["content_hash"], blocks_raw, v.get("text_md"), v.get("screenshot_url"),
                    v.get("screenshot_at"), v["source_url"], v.get("run_id"), class_raw, v.get("summary"),
                    v.get("review_status", "unreviewed"), v.get("review_note")
                ))
            return self.get_version(v["page_id"], v["version_no"])  # type: ignore
        finally:
            conn.close()

    def get_versions(self, page_id: str, limit: int = 100) -> list[dict]:
        conn = self._conn()
        try:
            cur = conn.execute("SELECT * FROM versions WHERE page_id = ? ORDER BY version_no ASC LIMIT ?", (page_id, limit))
            results = []
            for row in cur.fetchall():
                d = dict(row)
                try:
                    d["blocks"] = json.loads(d["blocks"] or "[]")
                except Exception:
                    d["blocks"] = []
                try:
                    d["classification"] = json.loads(d["classification"]) if d["classification"] else None
                except Exception:
                    pass
                results.append(d)
            return results
        finally:
            conn.close()

    def get_version(self, page_id: str, version_no: int) -> dict | None:
        conn = self._conn()
        try:
            cur = conn.execute("SELECT * FROM versions WHERE page_id = ? AND version_no = ?", (page_id, version_no))
            row = cur.fetchone()
            if not row:
                return None
            d = dict(row)
            try:
                d["blocks"] = json.loads(d["blocks"] or "[]")
            except Exception:
                d["blocks"] = []
            try:
                d["classification"] = json.loads(d["classification"]) if d["classification"] else None
            except Exception:
                pass
            return d
        finally:
            conn.close()

    def update_version(self, version_id: str, updates: dict) -> dict | None:
        conn = self._conn()
        try:
            set_clauses = []
            params = []
            for k, v in updates.items():
                if k in ("id", "page_id", "version_no"):
                    continue
                set_clauses.append(f"{k} = ?")
                if k in ("blocks", "classification") and not isinstance(v, str) and v is not None:
                    params.append(json.dumps(v))
                else:
                    params.append(v)
            if not set_clauses:
                return None
            params.append(version_id)
            with conn:
                conn.execute(f"UPDATE versions SET {', '.join(set_clauses)} WHERE id = ?", tuple(params))
            cur = conn.execute("SELECT * FROM versions WHERE id = ?", (version_id,))
            row = cur.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_latest_version(self, page_id: str) -> dict | None:
        conn = self._conn()
        try:
            cur = conn.execute("SELECT * FROM versions WHERE page_id = ? ORDER BY version_no DESC LIMIT 1", (page_id,))
            row = cur.fetchone()
            if not row:
                return None
            d = dict(row)
            try:
                d["blocks"] = json.loads(d["blocks"] or "[]")
            except Exception:
                d["blocks"] = []
            try:
                d["classification"] = json.loads(d["classification"]) if d["classification"] else None
            except Exception:
                pass
            return d
        finally:
            conn.close()

    def get_version_asof(self, page_id: str, dt_iso: str) -> dict | None:
        conn = self._conn()
        try:
            cur = conn.execute(
                "SELECT * FROM versions WHERE page_id = ? AND observed_at <= ? ORDER BY observed_at DESC LIMIT 1",
                (page_id, dt_iso)
            )
            row = cur.fetchone()
            if not row:
                return None
            d = dict(row)
            try:
                d["blocks"] = json.loads(d["blocks"] or "[]")
            except Exception:
                d["blocks"] = []
            try:
                d["classification"] = json.loads(d["classification"]) if d["classification"] else None
            except Exception:
                pass
            return d
        finally:
            conn.close()

    def add_diff(self, d: dict) -> dict:
        conn = self._conn()
        try:
            diff_id = d.get("id") or str(uuid.uuid4())
            now_utc = d.get("created_at") or datetime.now(timezone.utc).isoformat()
            diff_raw = json.dumps(d.get("diff", {})) if not isinstance(d.get("diff"), str) else d.get("diff")
            with conn:
                conn.execute("""
                    INSERT INTO diffs (id, page_id, from_version, to_version, diff, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (diff_id, d["page_id"], d["from_version"], d["to_version"], diff_raw, now_utc))
            return {
                "id": diff_id,
                "page_id": d["page_id"],
                "from_version": d["from_version"],
                "to_version": d["to_version"],
                "diff": d.get("diff", {}),
                "created_at": now_utc
            }
        finally:
            conn.close()

    def get_check_by_run_id(self, run_id: str) -> dict | None:
        conn = self._conn()
        try:
            cur = conn.execute("SELECT * FROM checks WHERE run_id = ?", (run_id,))
            row = cur.fetchone()
            if not row:
                return None
            d = dict(row)
            try:
                d["detail"] = json.loads(d["detail"] or "{}")
            except Exception:
                d["detail"] = {}
            return d
        finally:
            conn.close()

    def get_diff(self, page_id: str, from_v: int, to_v: int) -> dict | None:
        conn = self._conn()
        try:
            cur = conn.execute(
                "SELECT * FROM diffs WHERE page_id = ? AND from_version = ? AND to_version = ?",
                (page_id, from_v, to_v)
            )
            row = cur.fetchone()
            if not row:
                return None
            res = dict(row)
            try:
                res["diff"] = json.loads(res["diff"])
            except Exception:
                pass
            return res
        finally:
            conn.close()

    def add_check(self, check: dict) -> dict:
        conn = self._conn()
        try:
            chk_id = check.get("id") or str(uuid.uuid4())
            trigger = check.get("trigger") or check.get("detail", {}).get("trigger", "scheduled")
            detail_dict = dict(check.get("detail", {})) if isinstance(check.get("detail"), dict) else {}
            detail_dict["trigger"] = trigger
            detail_raw = json.dumps(detail_dict)
            with conn:
                conn.execute("""
                    INSERT OR IGNORE INTO checks (id, page_id, observed_at, run_id, outcome, detail, trigger)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    chk_id, check["page_id"], check["observed_at"], check.get("run_id"),
                    check["outcome"], detail_raw, trigger
                ))
            return {
                "id": chk_id,
                "page_id": check["page_id"],
                "observed_at": check["observed_at"],
                "run_id": check.get("run_id"),
                "outcome": check["outcome"],
                "trigger": trigger,
                "detail": detail_dict
            }
        finally:
            conn.close()

    def get_checks(self, page_id: str | None = None, limit: int = 100) -> list[dict]:
        conn = self._conn()
        try:
            if page_id:
                cur = conn.execute("SELECT * FROM checks WHERE page_id = ? ORDER BY observed_at DESC LIMIT ?", (page_id, limit))
            else:
                cur = conn.execute("SELECT * FROM checks ORDER BY observed_at DESC LIMIT ?", (limit,))
            res = []
            for row in cur.fetchall():
                d = dict(row)
                try:
                    d["detail"] = json.loads(d["detail"] or "{}")
                except Exception:
                    d["detail"] = {}
                d["trigger"] = d.get("trigger") or d.get("detail", {}).get("trigger", "scheduled")
                res.append(d)
            return res
        finally:
            conn.close()

    def add_suggestion(self, s: dict) -> dict:
        conn = self._conn()
        try:
            sid = s.get("id") or str(uuid.uuid4())
            s_at = s.get("suggested_at") or datetime.now(timezone.utc).isoformat()
            with conn:
                conn.execute("""
                    INSERT INTO suggestions (id, url, title, note, suggested_at, status)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (sid, s["url"], s.get("title"), s.get("note"), s_at, s.get("status", "pending_approval")))
            return self.get_suggestion(sid)  # type: ignore
        finally:
            conn.close()

    def get_suggestion(self, suggestion_id: str) -> dict | None:
        conn = self._conn()
        try:
            cur = conn.execute("SELECT * FROM suggestions WHERE id = ?", (suggestion_id,))
            row = cur.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def list_suggestions(self, limit: int = 100) -> list[dict]:
        conn = self._conn()
        try:
            cur = conn.execute("SELECT * FROM suggestions ORDER BY suggested_at DESC LIMIT ?", (limit,))
            return [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()


class SupabaseStore:
    """Supabase REST Postgres backend for Time Machine."""
    def __init__(self):
        self.sb_url = os.getenv("SUPABASE_URL")
        self.sb_key = os.getenv("SUPABASE_KEY")
        if not (self.sb_url and self.sb_key):
            cfg = dotenv_values(".env")
            self.sb_url = self.sb_url or cfg.get("SUPABASE_URL")
            self.sb_key = self.sb_key or cfg.get("SUPABASE_KEY")
        self.headers = {
            "apikey": self.sb_key or "",
            "Authorization": f"Bearer {self.sb_key or ''}",
            "Content-Type": "application/json",
            "Prefer": "return=representation"
        }
        self.client = httpx.Client(timeout=15.0)

    def _post(self, table: str, data: dict) -> dict:
        url = f"{self.sb_url}/rest/v1/{table}"
        resp = self.client.post(url, headers=self.headers, json=data)
        if resp.status_code in (200, 201):
            res = resp.json()
            return res[0] if isinstance(res, list) and res else data
        raise RuntimeError(f"Supabase insert into {table} failed: {resp.status_code} {resp.text}")

    def add_page(self, page: dict) -> dict:
        p = dict(page)
        if "id" not in p:
            p["id"] = str(uuid.uuid4())
        return self._post("pages", p)

    def get_page(self, page_id: str) -> dict | None:
        url = f"{self.sb_url}/rest/v1/pages?id=eq.{page_id}"
        resp = self.client.get(url, headers=self.headers)
        if resp.status_code == 200:
            arr = resp.json()
            return arr[0] if arr else None
        return None

    def get_page_by_url(self, page_url: str) -> dict | None:
        url = f"{self.sb_url}/rest/v1/pages?url=eq.{page_url}"
        resp = self.client.get(url, headers=self.headers)
        if resp.status_code == 200:
            arr = resp.json()
            return arr[0] if arr else None
        return None

    def list_pages(self, active_only: bool = False) -> list[dict]:
        url = f"{self.sb_url}/rest/v1/pages?order=added_at.asc"
        if active_only:
            url += "&active=eq.true"
        resp = self.client.get(url, headers=self.headers)
        if resp.status_code == 200:
            return resp.json()
        return []

    def delete_page(self, page_id: str) -> bool:
        url = f"{self.sb_url}/rest/v1/pages?id=eq.{page_id}"
        resp = self.client.delete(url, headers=self.headers)
        return resp.status_code in (200, 204)

    def update_page(self, page_id: str, updates: dict) -> dict | None:
        url = f"{self.sb_url}/rest/v1/pages?id=eq.{page_id}"
        resp = self.client.patch(url, headers=self.headers, json=updates)
        if resp.status_code == 200:
            arr = resp.json()
            return arr[0] if arr else None
        return None

    def add_version(self, version: dict) -> dict:
        return self._post("versions", version)

    def get_versions(self, page_id: str, limit: int = 100) -> list[dict]:
        url = f"{self.sb_url}/rest/v1/versions?page_id=eq.{page_id}&order=version_no.asc&limit={limit}"
        resp = self.client.get(url, headers=self.headers)
        if resp.status_code == 200:
            return resp.json()
        return []

    def get_version(self, page_id: str, version_no: int) -> dict | None:
        url = f"{self.sb_url}/rest/v1/versions?page_id=eq.{page_id}&version_no=eq.{version_no}"
        resp = self.client.get(url, headers=self.headers)
        if resp.status_code == 200:
            arr = resp.json()
            return arr[0] if arr else None
        return None

    def update_version(self, version_id: str, updates: dict) -> dict | None:
        conn = self._conn()
        try:
            set_clauses = []
            params = []
            for k, v in updates.items():
                if k in ("id", "page_id", "version_no"):
                    continue
                set_clauses.append(f"{k} = ?")
                if k in ("blocks", "classification") and not isinstance(v, str) and v is not None:
                    params.append(json.dumps(v))
                else:
                    params.append(v)
            if not set_clauses:
                return None
            params.append(version_id)
            with conn:
                conn.execute(f"UPDATE versions SET {', '.join(set_clauses)} WHERE id = ?", tuple(params))
            cur = conn.execute("SELECT * FROM versions WHERE id = ?", (version_id,))
            row = cur.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_latest_version(self, page_id: str) -> dict | None:
        url = f"{self.sb_url}/rest/v1/versions?page_id=eq.{page_id}&order=version_no.desc&limit=1"
        resp = self.client.get(url, headers=self.headers)
        if resp.status_code == 200:
            arr = resp.json()
            return arr[0] if arr else None
        return None

    def get_version_asof(self, page_id: str, dt_iso: str) -> dict | None:
        url = f"{self.sb_url}/rest/v1/versions?page_id=eq.{page_id}&observed_at=lte.{dt_iso}&order=observed_at.desc&limit=1"
        resp = self.client.get(url, headers=self.headers)
        if resp.status_code == 200:
            arr = resp.json()
            return arr[0] if arr else None
        return None

    def add_diff(self, d: dict) -> dict:
        return self._post("diffs", d)

    def get_check_by_run_id(self, run_id: str) -> dict | None:
        url = f"{self.sb_url}/rest/v1/checks?run_id=eq.{run_id}&limit=1"
        resp = self.client.get(url, headers=self.headers)
        if resp.status_code == 200:
            arr = resp.json()
            return arr[0] if arr else None
        return None

    def get_diff(self, page_id: str, from_v: int, to_v: int) -> dict | None:
        url = f"{self.sb_url}/rest/v1/diffs?page_id=eq.{page_id}&from_version=eq.{from_v}&to_version=eq.{to_v}"
        resp = self.client.get(url, headers=self.headers)
        if resp.status_code == 200:
            arr = resp.json()
            return arr[0] if arr else None
        return None

    def add_check(self, check: dict) -> dict:
        chk = dict(check)
        trigger = chk.get("trigger") or chk.get("detail", {}).get("trigger", "scheduled")
        if "detail" in chk and isinstance(chk["detail"], dict):
            chk["detail"]["trigger"] = trigger
        elif "detail" not in chk:
            chk["detail"] = {"trigger": trigger}
        chk["trigger"] = trigger
        try:
            res = self._post("checks", chk)
            res["trigger"] = trigger
            return res
        except Exception as e:
            err_str = str(e)
            if "trigger" in err_str and ("schema cache" in err_str or "does not exist" in err_str):
                chk_no_trig = dict(chk)
                chk_no_trig.pop("trigger", None)
                res = self._post("checks", chk_no_trig)
                res["trigger"] = trigger
                return res
            raise

    def get_checks(self, page_id: str | None = None, limit: int = 100) -> list[dict]:
        url = f"{self.sb_url}/rest/v1/checks?order=observed_at.desc&limit={limit}"
        if page_id:
            url += f"&page_id=eq.{page_id}"
        resp = self.client.get(url, headers=self.headers)
        if resp.status_code == 200:
            arr = resp.json()
            for c in arr:
                c["trigger"] = c.get("trigger") or (c.get("detail") or {}).get("trigger", "scheduled")
            return arr
        return []

    def add_suggestion(self, s: dict) -> dict:
        sid = s.get("id") or str(uuid.uuid4())
        s_at = s.get("suggested_at") or datetime.now(timezone.utc).isoformat()
        rec = {
            "id": sid,
            "url": s["url"],
            "title": s.get("title"),
            "note": s.get("note"),
            "suggested_at": s_at,
            "status": s.get("status", "pending_approval")
        }
        try:
            return self._post("suggestions", rec)
        except Exception:
            return rec

    def list_suggestions(self, limit: int = 100) -> list[dict]:
        url = f"{self.sb_url}/rest/v1/suggestions?order=suggested_at.desc&limit={limit}"
        try:
            resp = self.client.get(url, headers=self.headers)
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        return []


def get_store(backend: str | None = None) -> SqliteStore | SupabaseStore:
    """Factory selecting the storage backend according to STORE_BACKEND env var."""
    b = (backend or os.getenv("STORE_BACKEND", "sqlite")).strip().lower()
    if b == "supabase":
        return SupabaseStore()
    return SqliteStore()


class ScreenshotStore:
    """Manages screenshot storage in Supabase Storage bucket 'shots' with local fallback."""
    @staticmethod
    def _get_supabase_creds():
        sb_url = os.getenv("SUPABASE_URL")
        sb_key = os.getenv("SUPABASE_KEY")
        if not (sb_url and sb_key):
            cfg = dotenv_values(".env")
            sb_url = sb_url or cfg.get("SUPABASE_URL")
            sb_key = sb_key or cfg.get("SUPABASE_KEY")
        return sb_url, sb_key

    @classmethod
    def put(cls, image_bytes: bytes, key: str) -> str:
        """Uploads image bytes to Supabase Storage bucket 'shots'; falls back to local /data/shots."""
        if not key.endswith(".jpg") and not key.endswith(".jpeg") and not key.endswith(".png"):
            key = f"{key}.jpg"

        sb_url, sb_key = cls._get_supabase_creds()
        if sb_url and sb_key:
            try:
                upload_url = f"{sb_url}/storage/v1/object/{BUCKET_NAME}/{key}"
                headers = {
                    "apikey": sb_key,
                    "Authorization": f"Bearer {sb_key}",
                    "Content-Type": "image/jpeg",
                    "x-upsert": "true"
                }
                with httpx.Client(timeout=15) as client:
                    resp = client.post(upload_url, headers=headers, content=image_bytes)
                    if resp.status_code in (200, 201):
                        return f"{sb_url}/storage/v1/object/public/{BUCKET_NAME}/{key}"
            except Exception:
                pass

        # Fallback to local storage
        os.makedirs(LOCAL_SHOTS_DIR, exist_ok=True)
        local_path = os.path.join(LOCAL_SHOTS_DIR, key)
        with open(local_path, "wb") as f:
            f.write(image_bytes)
        return local_path

    @classmethod
    def get(cls, key: str) -> bytes | None:
        """Retrieves screenshot bytes from local storage."""
        local_path = os.path.join(LOCAL_SHOTS_DIR, key)
        if os.path.exists(local_path):
            with open(local_path, "rb") as f:
                return f.read()
        return None
