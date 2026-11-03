"""
tinycore/store.py
SQLite storage abstraction (WAL mode) and ScreenshotStore (Supabase Storage with local /data/shots fallback).
"""

import os
import sqlite3
import httpx
from dotenv import dotenv_values

DB_PATH = os.path.join("data", "app.db")
LOCAL_SHOTS_DIR = os.path.join("data", "shots")
BUCKET_NAME = "screenshots"


def get_db(db_path: str = DB_PATH) -> sqlite3.Connection:
    """Returns a SQLite connection configured with WAL mode and row factory."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db(schema_path: str = os.path.join("infra", "schema.sql")):
    """Initializes SQLite schema from SQL file."""
    if not os.path.exists(schema_path):
        return
    with open(schema_path, "r", encoding="utf-8") as f:
        sql = f.read()
    conn = get_db()
    with conn:
        conn.executescript(sql)


class ScreenshotStore:
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
        """Uploads image bytes to Supabase Storage; falls back to local /data/shots."""
        if not key.endswith(".jpg") and not key.endswith(".jpeg") and not key.endswith(".png"):
            key = f"{key}.jpg"

        sb_url, sb_key = cls._get_supabase_creds()
        if sb_url and sb_key:
            try:
                upload_url = f"{sb_url}/storage/v1/object/{BUCKET_NAME}/{key}"
                headers = {
                    "apikey": sb_key,
                    "Authorization": f"Bearer {sb_key}",
                    "Content-Type": "image/jpeg"
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
