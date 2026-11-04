"""
tests/test_timemachine_capture.py
Unit tests for Time Machine capture:
- add_page() flow (monitor create, v1 baseline, screenshot, check outcome)
- Idempotency on repeated add_page() calls
- Quota enforcement
"""

import pytest
from unittest.mock import MagicMock
from tinycore.store import SqliteStore
from tinycore.tf_client import MonitorCreateResult
from tinycore.ledger import QuotaExceeded
from apps.timemachine.capture import add_page


def test_add_page_flow_and_idempotency(tmp_path):
    db_file = str(tmp_path / "capture_test.db")
    store = SqliteStore(db_path=db_file)

    mock_tf = MagicMock()
    mock_tf.monitors_list.return_value = []
    mock_tf.monitor_create.return_value = MonitorCreateResult(
        monitor_id="mon-test-999",
        run={
            "id": "run-test-999",
            "created_at": "2026-11-03T12:00:00Z",
            "results": [{"text": "# Pricing Table\n| Plan | Price |\n| Basic | $10/mo |"}]
        }
    )
    mock_tf.screenshot.return_value = b"\xff\xd8\xff\xe0mockjpegbytes"

    # 1. First add_page call
    res1 = add_page(
        url="https://acme.com/pricing",
        title="Acme Pricing",
        category="ai-developer-tools",
        cron="15 */6 * * *",
        store=store,
        tf=mock_tf
    )

    assert res1["is_new"] is True
    page = res1["page"]
    v1 = res1["version"]

    assert page["url"] == "https://acme.com/pricing"
    assert page["monitor_id"] == "mon-test-999"
    assert page["history_starts_at"] == "2026-11-03T12:00:00Z"
    assert page["schedule_cron"] == "15 */6 * * *"

    assert v1["version_no"] == 1
    assert v1["observed_at"] == "2026-11-03T12:00:00Z"
    assert len(v1["blocks"]) == 2
    assert v1["screenshot_url"] is not None

    # Check that check row exists
    checks = store.get_checks(page["id"])
    assert len(checks) == 1
    assert checks[0]["outcome"] == "baseline"

    # 2. Second add_page call with identical URL (Idempotency)
    res2 = add_page(
        url="https://acme.com/pricing",
        title="Acme Pricing",
        store=store,
        tf=mock_tf
    )
    assert res2["is_new"] is False
    assert res2["page"]["id"] == page["id"]
    # Ensure monitor_create was only called once
    assert mock_tf.monitor_create.call_count == 1


def test_add_page_quota_exceeded(tmp_path):
    db_file = str(tmp_path / "quota_test.db")
    store = SqliteStore(db_path=db_file)

    mock_tf = MagicMock()
    # Simulate 30 active Time Machine monitors already
    mock_tf.monitors_list.return_value = [
        {"id": f"m-{i}", "name": f"TM: Page {i}", "status": "active"} for i in range(30)
    ]

    with pytest.raises(QuotaExceeded):
        add_page(
            url="https://newsite.com/pricing",
            store=store,
            tf=mock_tf
        )
