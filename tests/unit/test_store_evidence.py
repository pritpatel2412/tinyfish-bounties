"""
tests/test_store_evidence.py
Tests SQLite store, WAL mode, ScreenshotStore, and Evidence creation.
"""

import os
from tinycore.store import get_db, init_db, ScreenshotStore
from tinycore.evidence import create_evidence, Evidence


def test_sqlite_wal_mode():
    init_db()
    conn = get_db()
    cur = conn.execute("PRAGMA journal_mode;")
    mode = cur.fetchone()[0]
    assert mode.lower() == "wal"


def test_evidence_creation_and_hash():
    content = "<html><body><h1>Pricing Page</h1></body></html>"
    ev = create_evidence(
        source_url="https://stripe.com/pricing",
        raw_content=content,
        endpoint_used="fetch"
    )
    assert isinstance(ev, Evidence)
    assert ev.source_url == "https://stripe.com/pricing"
    assert ev.html_sha256 is not None
    assert len(ev.html_sha256) == 64
    assert ev.checked_at is not None

    # Check persistence in SQLite
    conn = get_db()
    cur = conn.execute("SELECT * FROM evidence_receipts WHERE id = ?", (ev.id,))
    row = cur.fetchone()
    assert row is not None
    assert row["source_url"] == ev.source_url


def test_screenshot_store_local_fallback():
    dummy_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF"  # JPEG magic bytes
    key = "test_shot_123.jpg"
    saved_path = ScreenshotStore.put(dummy_bytes, key)
    assert os.path.exists(saved_path)

    retrieved = ScreenshotStore.get(key)
    assert retrieved == dummy_bytes
