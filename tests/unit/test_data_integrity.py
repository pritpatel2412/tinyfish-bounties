import os
import pytest
from pydantic import ValidationError
from tinycore.llm import compute_materiality, classify_change
from tinycore.store import SqliteStore
from apps.timemachine.api import ScreenshotValidator


def test_screenshot_validator_rejects_http_in_production(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    # http must fail
    with pytest.raises(ValidationError):
        ScreenshotValidator(screenshot_url="http://storage.example.com/shot.jpg")
    with pytest.raises(ValidationError):
        ScreenshotValidator(screenshot_url="/local/path/shot.jpg")
    
    # https must pass
    valid = ScreenshotValidator(screenshot_url="https://storage.example.com/shot.jpg")
    assert valid.screenshot_url == "https://storage.example.com/shot.jpg"


def test_deterministic_materiality_rules():
    # 1. Numeric delta in pricing -> 0.85
    diff_price = {
        "changed": [
            {
                "heading_path": "Pricing",
                "before": "Plan: $20",
                "after": "Plan: $30",
                "numeric_delta": [{"old": 20.0, "new": 30.0}]
            }
        ],
        "is_empty": False
    }
    mat, suspect = compute_materiality(diff_price, baseline_blocks_count=10)
    assert mat == 0.85
    assert suspect is False

    # 2. Suspect capture variance: >50 added blocks, 0 removed, total_before > 10 -> cap at 0.3
    diff_variance = {
        "added": [{"heading_path": "Footer", "text": f"item {i}"} for i in range(55)],
        "removed": [],
        "changed": [],
        "is_empty": False
    }
    mat_v, suspect_v = compute_materiality(diff_variance, baseline_blocks_count=20)
    assert suspect_v is True
    assert mat_v <= 0.30


def test_llm_never_defaults_to_price_on_error(monkeypatch):
    # When Groq fails completely, classification must fallback to "unclassified", NEVER "price"
    def mock_fail(*args, **kwargs):
        raise RuntimeError("Groq network timeout")

    monkeypatch.setattr("tinycore.llm._call_groq_json", mock_fail)
    
    diff = {
        "changed": [{"heading_path": "Terms", "before": "Old terms", "after": "New terms"}],
        "is_empty": False
    }
    res = classify_change(diff)
    assert res["type"] == "unclassified"
    assert res["type"] != "price"
    assert "1 changed" in res["summary"]


def test_trigger_provenance_in_store(tmp_path):
    db_file = str(tmp_path / "test_store_trigger.db")
    store = SqliteStore(db_path=db_file)
    p = store.add_page({"url": "https://example.com/test", "title": "Test"})
    
    # Baseline
    c1 = store.add_check({
        "page_id": p["id"],
        "observed_at": "2026-11-01T00:00:00Z",
        "run_id": "run_base",
        "outcome": "baseline",
        "trigger": "baseline"
    })
    assert c1["trigger"] == "baseline"

    # Scheduled
    c2 = store.add_check({
        "page_id": p["id"],
        "observed_at": "2026-11-01T06:00:00Z",
        "run_id": "run_sched",
        "outcome": "unchanged",
        "trigger": "scheduled"
    })
    assert c2["trigger"] == "scheduled"

    # Manual
    c3 = store.add_check({
        "page_id": p["id"],
        "observed_at": "2026-11-01T12:00:00Z",
        "run_id": "run_man",
        "outcome": "unchanged",
        "trigger": "manual"
    })
    assert c3["trigger"] == "manual"

    checks = store.get_checks(page_id=p["id"])
    triggers = {c["trigger"] for c in checks}
    assert triggers == {"baseline", "scheduled", "manual"}


def test_monitor_event_object_and_dict_compatibility():
    from unittest.mock import MagicMock
    from apps.timemachine.processor import process_single_event
    from tinycore.events import MonitorEvent

    ev = MonitorEvent(
        id="evt_test_123",
        received_at="2026-11-06T12:00:00Z",
        monitor_id="mon_test_123",
        run_id="run_test_123",
        payload={"url": "https://example.com", "status": "completed"}
    )

    # 1. MonitorEvent dict-like methods
    assert ev.get("id") == "evt_test_123"
    assert ev.get("missing", "default") == "default"
    assert ev["run_id"] == "run_test_123"
    d = ev.to_dict()
    assert isinstance(d, dict)
    assert d["id"] == "evt_test_123"

    # 2. process_single_event handles MonitorEvent object directly without AttributeError
    mock_store = MagicMock()
    mock_store.get_check_by_run_id.return_value = None
    mock_store.get_page_by_monitor_id.return_value = None
    mock_store.get_page_by_url.return_value = None
    mock_store.get_checks.return_value = []
    mock_tf = MagicMock()

    res = process_single_event(ev, store=mock_store, tf=mock_tf)
    assert res.get("status") == "skipped"
    assert res.get("reason") == "unknown_monitor"
