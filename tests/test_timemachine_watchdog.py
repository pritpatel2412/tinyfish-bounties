import pytest
from unittest.mock import MagicMock, patch

from tinycore.store import SqliteStore
from apps.timemachine.watchdog import run_watchdog_check, WatchdogWorker


@pytest.fixture
def test_store(tmp_path):
    db_file = str(tmp_path / "test_watchdog.db")
    return SqliteStore(db_path=db_file)


def test_watchdog_resumes_paused_monitors(test_store):
    mock_tf = MagicMock()
    mock_tf.wallet_get.return_value = {"available_balance": "25.50"}
    mock_tf.monitors_list.return_value = [
        {"id": "mon_01", "name": "Active Monitor", "status": "ACTIVE"},
        {"id": "mon_02", "name": "Paused Monitor", "status": "PAUSED"}
    ]

    res = run_watchdog_check(store=test_store, tf=mock_tf)

    assert res["wallet_balance_usd"] == 25.50
    assert res["wallet_alert"] is False
    assert "mon_02" in res["resumed_monitors"]
    mock_tf.monitor_resume.assert_called_once_with("mon_02")


def test_watchdog_wallet_low_alert(test_store):
    mock_tf = MagicMock()
    mock_tf.wallet_get.return_value = {"available_balance": "4.50"}
    mock_tf.monitors_list.return_value = []

    res = run_watchdog_check(store=test_store, tf=mock_tf)

    assert res["wallet_balance_usd"] == 4.50
    assert res["wallet_alert"] is True


def test_watchdog_drift_detection(test_store):
    # Add active page in DB whose monitor_id is not in live_monitors
    test_store.add_page({
        "url": "https://example.com/drifted",
        "monitor_id": "mon_ghost",
        "schedule_cron": "0 */6 * * *",
        "history_starts_at": "2026-11-01T00:00:00Z"
    })

    mock_tf = MagicMock()
    mock_tf.wallet_get.return_value = {"available_balance": "30.00"}
    mock_tf.monitors_list.return_value = [
        {"id": "mon_different", "status": "ACTIVE"}
    ]

    res = run_watchdog_check(store=test_store, tf=mock_tf)

    assert len(res["drift_pages"]) == 1
    assert res["drift_pages"][0]["monitor_id"] == "mon_ghost"


def test_watchdog_worker_run_now(test_store):
    mock_tf = MagicMock()
    mock_tf.wallet_get.return_value = {"available_balance": "30.00"}
    mock_tf.monitors_list.return_value = []

    worker = WatchdogWorker(interval_seconds=3600, store=test_store, tf=mock_tf)
    res = worker.run_now()
    assert worker.last_check_at is not None
    assert worker.last_result == res
