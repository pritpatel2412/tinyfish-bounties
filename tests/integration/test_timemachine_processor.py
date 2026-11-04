import os
import json
import uuid
import pytest
from unittest.mock import MagicMock, patch

from tinycore.store import SqliteStore
from tinycore.normalize import markdown_to_blocks, compute_norm_hash
from apps.timemachine.processor import (
    process_single_event,
    process_pending_events,
    insert_incoming_event,
    get_unprocessed_events,
    mark_event_processed,
    ProcessorWorker
)


@pytest.fixture
def test_store(tmp_path):
    db_file = str(tmp_path / "test_processor.db")
    store = SqliteStore(db_path=db_file)
    return store


@pytest.fixture
def seeded_page(test_store):
    page = test_store.add_page({
        "url": "https://example.com/pricing",
        "title": "Example Pricing",
        "category": "pricing",
        "monitor_id": "mon_test_123",
        "schedule_cron": "0 */6 * * *",
        "interval_label": "every 6h",
        "history_starts_at": "2026-11-01T00:00:00Z"
    })
    # Add initial v1 baseline
    baseline_text = "# Example Pricing\n\nPlan Pro: $49/mo"
    blocks = markdown_to_blocks(baseline_text)
    h = compute_norm_hash(baseline_text)
    test_store.add_version({
        "page_id": page["id"],
        "version_no": 1,
        "observed_at": "2026-11-01T00:00:00Z",
        "processed_at": "2026-11-01T00:01:00Z",
        "lag_seconds": 60,
        "content_hash": h,
        "blocks": [b.__dict__ for b in blocks],
        "text_md": baseline_text,
        "screenshot_url": "https://storage.example.com/baseline.jpg",
        "screenshot_at": "2026-11-01T00:01:00Z",
        "source_url": page["url"],
        "run_id": "run_v1_baseline",
        "classification": {"type": "baseline", "materiality": 1.0, "summary": "Baseline"},
        "summary": "Baseline"
    })
    test_store.add_check({
        "page_id": page["id"],
        "observed_at": "2026-11-01T00:00:00Z",
        "run_id": "run_v1_baseline",
        "outcome": "baseline",
        "detail": {"version_no": 1}
    })
    return page


def test_idempotency_on_run_id(test_store, seeded_page):
    # Event with already-handled run_id
    ev_id = insert_incoming_event({
        "monitor_id": "mon_test_123",
        "run_id": "run_v1_baseline",
        "payload": {
            "id": "run_v1_baseline",
            "monitor_id": "mon_test_123",
            "results": [{"text": "# Example Pricing\n\nPlan Pro: $49/mo"}]
        }
    }, store=test_store)

    events = get_unprocessed_events(test_store)
    assert len(events) == 1

    res = process_single_event(events[0], store=test_store)
    assert res["status"] == "skipped"
    assert res["reason"] == "already_processed"

    # Event should now be marked processed
    unprocessed = get_unprocessed_events(test_store)
    assert len(unprocessed) == 0


def test_unchanged_content(test_store, seeded_page):
    # Identical content -> hash match -> checks(outcome='unchanged')
    run_id = f"run_{uuid.uuid4().hex[:8]}"
    ev_id = insert_incoming_event({
        "monitor_id": "mon_test_123",
        "run_id": run_id,
        "payload": {
            "id": run_id,
            "monitor_id": "mon_test_123",
            "created_at": "2026-11-02T12:00:00Z",
            "results": [{"text": "# Example Pricing\n\nPlan Pro: $49/mo"}]
        }
    }, store=test_store)

    events = get_unprocessed_events(test_store)
    res = process_single_event(events[0], store=test_store)

    assert res["status"] == "success"
    assert res["outcome"] == "unchanged"

    # Verify check
    checks = test_store.get_checks(page_id=seeded_page["id"])
    assert any(c["run_id"] == run_id and c["outcome"] == "unchanged" for c in checks)

    # Page last_checked_at updated
    pages = test_store.list_pages()
    assert pages[0]["last_checked_at"] == "2026-11-02T12:00:00Z"


def test_noise_filtered_content(test_store, seeded_page):
    # Content has transient timestamp/last updated noise which differ_blocks filters out
    run_id = f"run_{uuid.uuid4().hex[:8]}"
    noise_text = "# Example Pricing\n\nPlan Pro: $49/mo\n\nLast updated: November 3, 2026"
    ev_id = insert_incoming_event({
        "monitor_id": "mon_test_123",
        "run_id": run_id,
        "payload": {
            "id": run_id,
            "monitor_id": "mon_test_123",
            "created_at": "2026-11-03T10:00:00Z",
            "results": [{"text": noise_text}]
        }
    }, store=test_store)

    events = get_unprocessed_events(test_store)
    res = process_single_event(events[0], store=test_store)

    # Note: If markdown_to_blocks removes the last updated line completely, diff is empty -> noise
    assert res["status"] == "success"
    assert res["outcome"] in ("noise", "unchanged")

    checks = test_store.get_checks(page_id=seeded_page["id"])
    assert any(c["run_id"] == run_id and c["outcome"] in ("noise", "unchanged") for c in checks)


def test_material_change_detection(test_store, seeded_page):
    run_id = f"run_{uuid.uuid4().hex[:8]}"
    changed_text = "# Example Pricing\n\nPlan Pro: $79/mo\n\nNew Feature: AI Autopilot included."

    ev_id = insert_incoming_event({
        "monitor_id": "mon_test_123",
        "run_id": run_id,
        "payload": {
            "id": run_id,
            "monitor_id": "mon_test_123",
            "created_at": "2026-11-03T14:00:00Z",
            "results": [{"text": changed_text}]
        }
    }, store=test_store)

    mock_tf = MagicMock()
    mock_fetch_res = MagicMock()
    mock_fetch_res.text = changed_text
    mock_tf.fetch.return_value = mock_fetch_res
    mock_tf.screenshot.return_value = b"\xff\xd8\xff\xe0mock_screenshot_bytes"

    events = get_unprocessed_events(test_store)
    with patch("tinycore.store.ScreenshotStore.put", return_value="https://storage.example.com/v2.jpg"):
        with patch("apps.timemachine.processor.classify_change") as mock_classify:
            mock_classify.return_value = {
                "category": "pricing",
                "summary": "Pro plan increased from $49/mo to $79/mo",
                "materiality": 0.9,
                "confidence": 0.95
            }
            res = process_single_event(events[0], store=test_store, tf=mock_tf)

    assert res["status"] == "success"
    assert res["outcome"] == "changed"
    assert res["version_no"] == 2

    # Check that version 2 was inserted
    latest = test_store.get_latest_version(seeded_page["id"])
    assert latest["version_no"] == 2
    assert latest["screenshot_url"] == "https://storage.example.com/v2.jpg"
    assert latest["summary"] == "Pro plan increased from $49/mo to $79/mo"

    # Check diff record was created
    diff_rec = test_store.get_diff(seeded_page["id"], 1, 2)
    assert diff_rec is not None
    assert "diff" in diff_rec

    # Check checks table
    chk = test_store.get_check_by_run_id(run_id)
    assert chk is not None
    assert chk["outcome"] == "changed"
    assert chk["detail"]["version_no"] == 2


def test_run_error_handling(test_store, seeded_page):
    run_id = f"run_{uuid.uuid4().hex[:8]}"
    ev_id = insert_incoming_event({
        "monitor_id": "mon_test_123",
        "run_id": run_id,
        "payload": {
            "id": run_id,
            "monitor_id": "mon_test_123",
            "status": "FAILED",
            "errors": ["DNS resolution failure"]
        }
    }, store=test_store)

    events = get_unprocessed_events(test_store)
    res = process_single_event(events[0], store=test_store)

    assert res["status"] == "error"
    assert res["outcome"] == "error"

    chk = test_store.get_check_by_run_id(run_id)
    assert chk is not None
    assert chk["outcome"] == "error"
    assert "DNS resolution failure" in str(chk["detail"])


def test_processor_worker_cycle(test_store, seeded_page):
    worker = ProcessorWorker(interval_seconds=60, store=test_store)
    assert worker.events_processed_total == 0

    # Wake with no events
    count = worker.wake()
    assert count == 0
    assert worker.last_run_at is not None

