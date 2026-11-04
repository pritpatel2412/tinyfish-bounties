"""
tests/test_timemachine_store.py
Tests SqliteStore for Time Machine:
pages, versions, diffs, checks, as-of querying, and get_store factory.
"""

import os
from datetime import datetime, timezone
from tinycore.store import SqliteStore, get_store


def test_sqlite_store_pages_crud(tmp_path):
    db_file = str(tmp_path / "tm_test.db")
    store = SqliteStore(db_path=db_file)

    # 1. Add page
    page = {
        "url": "https://example.com/pricing",
        "title": "Example Pricing",
        "category": "ai-developer-tools",
        "schedule_cron": "0 */6 * * *",
        "interval_label": "6h"
    }
    p_added = store.add_page(page)
    assert p_added["url"] == page["url"]
    assert p_added["active"] is True
    page_id = p_added["id"]

    # 2. Get by ID and URL
    assert store.get_page(page_id)["title"] == "Example Pricing"
    assert store.get_page_by_url("https://example.com/pricing")["id"] == page_id

    # 3. List pages
    all_pages = store.list_pages()
    assert len(all_pages) == 1

    # 4. Update page
    updated = store.update_page(page_id, {"status": "paused", "active": False})
    assert updated["status"] == "paused"
    assert updated["active"] is False
    assert len(store.list_pages(active_only=True)) == 0


def test_sqlite_store_versions_and_asof(tmp_path):
    db_file = str(tmp_path / "tm_test2.db")
    store = SqliteStore(db_path=db_file)

    p = store.add_page({"url": "https://service.com/pricing", "title": "Service"})
    page_id = p["id"]

    # Version 1 observed at 10:00:00Z
    v1 = {
        "page_id": page_id,
        "version_no": 1,
        "observed_at": "2026-11-01T10:00:00Z",
        "content_hash": "hash111",
        "blocks": [{"id": "b1", "text": "Plan: $10"}],
        "text_md": "# Plan: $10",
        "source_url": "https://service.com/pricing"
    }
    store.add_version(v1)

    # Version 2 observed at 14:00:00Z
    v2 = {
        "page_id": page_id,
        "version_no": 2,
        "observed_at": "2026-11-01T14:00:00Z",
        "content_hash": "hash222",
        "blocks": [{"id": "b1", "text": "Plan: $20"}],
        "text_md": "# Plan: $20",
        "source_url": "https://service.com/pricing"
    }
    store.add_version(v2)

    # Diff between 1 and 2
    store.add_diff({
        "page_id": page_id,
        "from_version": 1,
        "to_version": 2,
        "diff": {"changed": [{"unit": "$", "old": 10.0, "new": 20.0}]}
    })

    # Query checks
    latest = store.get_latest_version(page_id)
    assert latest["version_no"] == 2

    # As-of tests:
    # 1. Before V1 -> None
    asof_early = store.get_version_asof(page_id, "2026-11-01T09:00:00Z")
    assert asof_early is None

    # 2. Between V1 and V2 -> V1
    asof_mid = store.get_version_asof(page_id, "2026-11-01T12:00:00Z")
    assert asof_mid["version_no"] == 1

    # 3. At or after V2 -> V2
    asof_late = store.get_version_asof(page_id, "2026-11-01T14:00:00Z")
    assert asof_late["version_no"] == 2

    # Diff retrieval
    diff_obj = store.get_diff(page_id, 1, 2)
    assert diff_obj is not None
    assert "changed" in diff_obj["diff"]


def test_sqlite_store_checks(tmp_path):
    db_file = str(tmp_path / "tm_test3.db")
    store = SqliteStore(db_path=db_file)

    p = store.add_page({"url": "https://site.org", "title": "Site"})
    page_id = p["id"]

    chk1 = {
        "page_id": page_id,
        "observed_at": "2026-11-02T08:00:00Z",
        "run_id": "run-001",
        "outcome": "baseline",
        "detail": {"status": "ok"}
    }
    store.add_check(chk1)

    chk2 = {
        "page_id": page_id,
        "observed_at": "2026-11-02T12:00:00Z",
        "run_id": "run-002",
        "outcome": "unchanged",
        "detail": {"hash": "hash111"}
    }
    store.add_check(chk2)

    checks = store.get_checks(page_id)
    assert len(checks) == 2
    assert checks[0]["outcome"] == "unchanged"  # Most recent first
    assert checks[1]["outcome"] == "baseline"


def test_get_store_factory(monkeypatch):
    monkeypatch.setenv("STORE_BACKEND", "sqlite")
    st = get_store()
    assert isinstance(st, SqliteStore)



def test_sqlite_store_delete_page(tmp_path):
    db_file = str(tmp_path / "tm_delete.db")
    store = SqliteStore(db_path=db_file)
    p = store.add_page({"url": "https://delete-me.com", "title": "Delete Me"})
    page_id = p["id"]
    assert store.get_page(page_id) is not None
    assert store.delete_page(page_id) is True
    assert store.get_page(page_id) is None
    assert store.delete_page(page_id) is False
