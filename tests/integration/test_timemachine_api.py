import os
import json
import uuid
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from tinycore.store import SqliteStore
from tinycore.normalize import markdown_to_blocks, compute_norm_hash
from apps.timemachine.api import app
from apps.timemachine.processor import processor_worker


@pytest.fixture
def test_db_store(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_api.db")
    store = SqliteStore(db_path=db_file)
    # Monkeypatch get_store in apps.timemachine.api and apps.timemachine.processor
    monkeypatch.setattr("apps.timemachine.api.get_store", lambda backend=None: store)
    monkeypatch.setattr("apps.timemachine.processor.get_store", lambda backend=None: store)
    processor_worker.store = store
    return store


@pytest.fixture
def client(test_db_store, monkeypatch):
    # Disable background worker thread during fast unit tests
    monkeypatch.setattr("apps.timemachine.api.processor_worker.start", lambda: None)
    monkeypatch.setattr("apps.timemachine.api.processor_worker.stop", lambda: None)
    monkeypatch.setattr("apps.timemachine.api.processor_worker.wake", lambda: 0)
    monkeypatch.setattr("apps.timemachine.api.watchdog_worker.start", lambda: None)
    monkeypatch.setattr("apps.timemachine.api.watchdog_worker.stop", lambda: None)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def seeded_page(test_db_store):
    page = test_db_store.add_page({
        "url": "https://example.com/pricing",
        "title": "Example Pricing",
        "category": "saas_pricing",
        "monitor_id": "mon_test_api",
        "schedule_cron": "0 */6 * * *",
        "interval_label": "every 6h",
        "history_starts_at": "2026-11-01T00:00:00Z"
    })

    # Version 1 (2026-11-01T00:00:00Z)
    text_v1 = "# Pricing\n\nStandard: $20/mo\n\nEnterprise: $100/mo"
    b_v1 = markdown_to_blocks(text_v1)
    test_db_store.add_version({
        "page_id": page["id"],
        "version_no": 1,
        "observed_at": "2026-11-01T00:00:00Z",
        "processed_at": "2026-11-01T00:01:00Z",
        "lag_seconds": 60,
        "content_hash": compute_norm_hash(text_v1),
        "blocks": [b.__dict__ for b in b_v1],
        "text_md": text_v1,
        "screenshot_url": "https://storage.example.com/v1.jpg",
        "screenshot_at": "2026-11-01T00:01:00Z",
        "source_url": page["url"],
        "run_id": "run_01",
        "classification": {"type": "baseline", "materiality": 1.0, "summary": "Baseline"},
        "summary": "Baseline snapshot"
    })
    test_db_store.add_check({
        "page_id": page["id"],
        "observed_at": "2026-11-01T00:00:00Z",
        "run_id": "run_01",
        "outcome": "baseline",
        "detail": {"version_no": 1}
    })

    # Version 2 (2026-11-02T12:00:00Z)
    text_v2 = "# Pricing\n\nStandard: $25/mo\n\nEnterprise: $120/mo"
    b_v2 = markdown_to_blocks(text_v2)
    test_db_store.add_version({
        "page_id": page["id"],
        "version_no": 2,
        "observed_at": "2026-11-02T12:00:00Z",
        "processed_at": "2026-11-02T12:01:00Z",
        "lag_seconds": 60,
        "content_hash": compute_norm_hash(text_v2),
        "blocks": [b.__dict__ for b in b_v2],
        "text_md": text_v2,
        "screenshot_url": "https://storage.example.com/v2.jpg",
        "screenshot_at": "2026-11-02T12:01:00Z",
        "source_url": page["url"],
        "run_id": "run_02",
        "review_status": "real",
        "classification": {"category": "pricing", "materiality": 0.8, "summary": "Price hike"},
        "summary": "Standard plan increased to $25"
    })
    test_db_store.add_check({
        "page_id": page["id"],
        "observed_at": "2026-11-02T12:00:00Z",
        "run_id": "run_02",
        "outcome": "changed",
        "detail": {"version_no": 2, "review_status": "real", "classification": {"materiality": 0.8, "summary": "Price hike"}}
    })
    test_db_store.add_diff({
        "page_id": page["id"],
        "from_version": 1,
        "to_version": 2,
        "diff": {
            "added": [],
            "removed": [],
            "changed": [{"before": "Standard: $20/mo", "after": "Standard: $25/mo", "numeric_delta": 5.0}],
            "stats": {"added": 0, "removed": 0, "changed": 1, "numeric_deltas": 1},
            "is_empty": False
        }
    })

    return page


def test_health_and_endpoint_usage(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in ("healthy", "degraded")
    assert "processor_heartbeat_age_s" in data
    assert "uptime_s" in data
    assert "version" in data
    assert "wallet_balance_usd" not in data
    assert "daily_spend_usd" not in data

    resp_admin = client.get("/admin/health")
    assert resp_admin.status_code == 200
    admin_data = resp_admin.json()
    assert "unprocessed_events_count" in admin_data
    assert "monitors_active" in admin_data
    assert "monitors_paused" in admin_data
    assert "store_backend" in admin_data
    assert "git_sha" in admin_data
    assert "ledger" in admin_data

    resp2 = client.get("/endpoint-usage")
    assert resp2.status_code == 200
    assert "total_calls" in resp2.json()


def test_post_pages_success(client, test_db_store):
    with patch("apps.timemachine.api.add_page") as mock_add:
        mock_add.return_value = {
            "status": "created",
            "page": {"id": "page_123", "url": "https://new.com", "title": "New"},
            "version": {"version_no": 1}
        }
        resp = client.post("/pages", json={"url": "https://new.com", "title": "New", "category": "saas_pricing"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "created"
        assert data["page"]["id"] == "page_123"


def test_get_pages_list(client, seeded_page):
    resp = client.get("/pages")
    assert resp.status_code == 200
    pages = resp.json()
    assert len(pages) == 1
    assert pages[0]["url"] == seeded_page["url"]
    assert pages[0]["version_count"] == 2


def test_get_page_detail(client, seeded_page):
    resp = client.get(f"/pages/{seeded_page['id']}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == seeded_page["id"]
    assert data["version_count"] == 2
    assert data["latest_version"]["version_no"] == 2


def test_get_page_timeline(client, seeded_page):
    resp = client.get(f"/pages/{seeded_page['id']}/timeline")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_versions"] == 2
    timeline = data["timeline"]
    assert timeline[0]["version_no"] == 2  # descending
    assert timeline[1]["version_no"] == 1


def test_get_asof_boundaries(client, seeded_page):
    p_id = seeded_page["id"]

    # 1. Query before history_starts_at (2026-11-01T00:00:00Z) -> 404 BEFORE_HISTORY
    resp_before = client.get(f"/pages/{p_id}/asof?date=2026-10-15T00:00:00Z")
    assert resp_before.status_code == 404
    err = resp_before.json()["error"]
    assert err["code"] == "BEFORE_HISTORY"
    assert "history_starts_at" in err

    # 2. Query exactly on baseline date -> Version 1
    resp_v1 = client.get(f"/pages/{p_id}/asof?date=2026-11-01T00:00:00Z")
    assert resp_v1.status_code == 200
    assert resp_v1.json()["version_no"] == 1

    # 3. Query between v1 and v2 (e.g. 2026-11-02T06:00:00Z) -> Version 1
    resp_between = client.get(f"/pages/{p_id}/asof?date=2026-11-02T06:00:00Z")
    assert resp_between.status_code == 200
    assert resp_between.json()["version_no"] == 1

    # 4. Query after v2 (e.g. 2026-11-03) -> Version 2
    resp_after = client.get(f"/pages/{p_id}/asof?date=2026-11-03")
    assert resp_after.status_code == 200
    assert resp_after.json()["version_no"] == 2


def test_get_diff_endpoints(client, seeded_page):
    p_id = seeded_page["id"]

    # Diff between version 1 and version 2 (integers)
    resp = client.get(f"/pages/{p_id}/diff?from=1&to=2")
    assert resp.status_code == 200
    data = resp.json()
    assert data["from_version"] == 1
    assert data["to_version"] == 2
    assert len(data["diff"]["changed"]) == 1

    # Diff between same version
    resp_same = client.get(f"/pages/{p_id}/diff?from=1&to=1")
    assert resp_same.status_code == 200
    assert resp_same.json()["diff"]["is_empty"] is True

    # Diff by dates
    resp_dates = client.get(f"/pages/{p_id}/diff?from=2026-11-01T01:00:00Z&to=2026-11-03T00:00:00Z")
    assert resp_dates.status_code == 200
    assert resp_dates.json()["from_version"] == 1
    assert resp_dates.json()["to_version"] == 2


def test_get_version_by_number(client, seeded_page):
    p_id = seeded_page["id"]
    resp = client.get(f"/pages/{p_id}/versions/2")
    assert resp.status_code == 200
    assert resp.json()["version_no"] == 2

    # Non-existent version
    resp_miss = client.get(f"/pages/{p_id}/versions/99")
    assert resp_miss.status_code == 404
    assert resp_miss.json()["error"]["code"] == "VERSION_NOT_FOUND"


def test_get_changes_feed(client, seeded_page):
    resp = client.get("/changes")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_changes"] == 1
    assert data["changes"][0]["version_no"] == 2
    assert data["changes"][0]["category"] == "saas_pricing"

    # Filter by min_materiality
    resp_filt = client.get("/changes?min_materiality=0.9")
    assert resp_filt.status_code == 200
    assert resp_filt.json()["total_changes"] == 0


def test_get_logs_feed(client, seeded_page):
    resp = client.get("/logs?limit=50")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 2
    outcomes = [l["outcome"] for l in data["logs"]]
    assert "baseline" in outcomes
    assert "changed" in outcomes


def test_export_json(client, seeded_page):
    resp = client.get("/export.json")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_pages"] == 1
    p = data["pages"][0]
    assert len(p["versions"]) == 2
    assert len(p["checks"]) == 2


def test_webhook_and_internal_wake(client, test_db_store):
    # Test POST /webhook
    hook_payload = {
        "id": "run_hook_test",
        "monitor_id": "mon_test_api",
        "results": [{"text": "Hello webhook"}]
    }
    resp = client.post("/webhook", json=hook_payload)
    assert resp.status_code == 200
    assert resp.json()["status"] == "received"

    # Test POST /internal/wake
    resp_wake = client.post("/internal/wake")
    assert resp_wake.status_code == 200
    assert resp_wake.json()["status"] == "ok"



def test_admin_token_and_delete_page(client, seeded_page, monkeypatch):
    page_id = seeded_page["id"]
    monkeypatch.setenv("ADMIN_TOKEN", "super_secret_token_123")

    # 1. POST /pages without token -> 401
    resp_unauth = client.post("/pages", json={"url": "https://unauth.com", "title": "Unauth"})
    assert resp_unauth.status_code == 401

    # 2. DELETE /pages/{id} with wrong token -> 401
    resp_wrong = client.delete(f"/pages/{page_id}", headers={"X-Admin-Token": "wrong"})
    assert resp_wrong.status_code == 401

    # 3. DELETE /pages/{id} with correct token -> 200
    with patch("apps.timemachine.api.TF") as mock_tf_cls:
        mock_tf = mock_tf_cls.return_value
        mock_tf.monitors_delete.return_value = {"ok": True}
        resp_del = client.delete(f"/pages/{page_id}", headers={"X-Admin-Token": "super_secret_token_123"})
        assert resp_del.status_code == 200
        assert resp_del.json()["deleted"] is True


def test_webhook_and_wake_secrets(client, monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "hook_sec_999")
    monkeypatch.setenv("WAKE_SECRET", "wake_sec_888")

    # Webhook unauthorized
    resp_wh_bad = client.post("/webhook", json={"id": "bad"})
    assert resp_wh_bad.status_code == 401

    # Webhook authorized via query param
    resp_wh_ok = client.post("/webhook?secret=hook_sec_999", json={"id": "ok"})
    assert resp_wh_ok.status_code == 200

    # Wake unauthorized
    resp_wake_bad = client.post("/internal/wake")
    assert resp_wake_bad.status_code == 401

    # Wake authorized via header
    resp_wake_ok = client.post("/internal/wake", headers={"X-Wake-Secret": "wake_sec_888"})
    assert resp_wake_ok.status_code == 200


def test_suggest_page(client, tmp_path, monkeypatch):
    from apps.timemachine.api import _suggest_rate_limits
    _suggest_rate_limits.clear()

    # 1. Valid suggestion with note
    resp = client.post("/suggest", json={
        "url": "https://stripe.com/pricing",
        "title": "Stripe Pricing",
        "note": "Crucial payment fee updates"
    })
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "queued"
    assert "Suggestions are reviewed before anything is tracked" in data["message"]
    assert data["suggestion"]["url"] == "https://stripe.com/pricing"
    assert data["suggestion"]["note"] == "Crucial payment fee updates"

    # 2. Invalid URL -> 400 INVALID_URL
    resp_bad = client.post("/suggest", json={"url": "not-a-url"})
    assert resp_bad.status_code == 400
    assert resp_bad.json()["error"]["code"] == "INVALID_URL"

    resp_bad_scheme = client.post("/suggest", json={"url": "ftp://example.com/pricing"})
    assert resp_bad_scheme.status_code == 400
    assert resp_bad_scheme.json()["error"]["code"] == "INVALID_URL"

    # 3. Rate limiting (max 5 per hour per IP)
    # 4 more calls -> hits 5 total
    for i in range(4):
        r = client.post("/suggest", json={"url": f"https://example{i}.com"})
        assert r.status_code == 202

    # 6th call from same IP -> 429 RATE_LIMITED
    resp_limited = client.post("/suggest", json={"url": "https://toomany.com"})
    assert resp_limited.status_code == 429
    assert resp_limited.json()["error"]["code"] == "RATE_LIMITED"


def test_stats_endpoint(client, seeded_page, test_db_store):
    # Add a scheduled check and a manual check to seeded_page
    test_db_store.add_check({
        "page_id": seeded_page["id"],
        "observed_at": "2026-11-04T12:00:00Z",
        "run_id": "run_stat_sched",
        "outcome": "unchanged",
        "trigger": "scheduled"
    })
    test_db_store.add_check({
        "page_id": seeded_page["id"],
        "observed_at": "2026-11-04T14:00:00Z",
        "run_id": "run_stat_man",
        "outcome": "unchanged",
        "trigger": "manual"
    })

    # Add a test page with checks to verify test pages are excluded
    test_p = test_db_store.add_page({
        "url": "https://internal.test/pricing",
        "title": "Internal Test Page",
        "category": "test",
        "is_test": 1
    })
    test_db_store.add_check({
        "page_id": test_p["id"],
        "observed_at": "2026-11-04T15:00:00Z",
        "run_id": "run_stat_test",
        "outcome": "changed",
        "trigger": "scheduled"
    })

    resp = client.get("/stats")
    assert resp.status_code == 200
    stats = resp.json()

    # Core keys
    assert "pages_tracked" in stats
    assert stats["pages_tracked"] == 1  # excludes test page
    assert stats["scheduled_checks_total"] >= 1
    assert stats["manual_checks_total"] >= 1
    assert stats["first_scheduled_check_at"] is not None
    assert "days_of_scheduled_history" in stats
    assert "confirmed_changes" in stats
    assert "unreviewed_changes" in stats
    assert stats["confirmed_changes"] >= 1  # Version 2 is marked real in seeded_page

    # Outcome counts map
    oc = stats["outcome_counts"]
    for expected_key in ("baseline", "unchanged", "noise", "unconfirmed", "changed", "error"):
        assert expected_key in oc
        assert isinstance(oc[expected_key], int)


def test_pages_contract_fields(client, seeded_page):
    resp = client.get("/pages")
    assert resp.status_code == 200
    pages = resp.json()
    assert len(pages) >= 1
    p = pages[0]
    required_keys = [
        "id", "url", "title", "category", "versions_count",
        "last_checked_at", "status", "history_starts_at", "interval_label"
    ]
    for k in required_keys:
        assert k in p, f"Missing required key '{k}' in /pages response"
    assert p["versions_count"] == 2
    assert p["status"] in ("active", "paused")


def test_cors_preflight_and_methods(client):
    origin = "https://time-machine.base44.app"

    # 1. GET /pages from Base44 origin -> allowed
    resp_get = client.get("/pages", headers={"Origin": origin})
    assert resp_get.status_code == 200
    assert resp_get.headers.get("access-control-allow-origin") == origin
    assert "GET" in resp_get.headers.get("access-control-allow-methods", "")

    # 2. OPTIONS /suggest preflight from Base44 origin -> allowed POST, OPTIONS
    resp_opt_suggest = client.options("/suggest", headers={
        "Origin": origin,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type"
    })
    assert resp_opt_suggest.status_code == 204
    assert resp_opt_suggest.headers.get("access-control-allow-origin") == origin
    assert "POST" in resp_opt_suggest.headers.get("access-control-allow-methods", "")

    # 3. OPTIONS /pages preflight with POST from Base44 origin -> GET, OPTIONS only (no POST)
    resp_opt_pages = client.options("/pages", headers={
        "Origin": origin,
        "Access-Control-Request-Method": "POST"
    })
    assert resp_opt_pages.status_code == 204
    assert resp_opt_pages.headers.get("access-control-allow-origin") == origin
    methods = resp_opt_pages.headers.get("access-control-allow-methods", "")
    assert "POST" not in methods
    assert "GET" in methods

