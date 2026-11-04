"""
tests/test_tf_live.py
Live tests for TinyFish client methods against real endpoints.
Marked with @pytest.mark.live.
"""

import pytest
from tinycore.tf_client import TF, FetchResult, AgentResult


@pytest.mark.live
def test_live_tf_fetch():
    tf = TF()
    res = tf.fetch("https://stripe.com/pricing", format="markdown")
    assert isinstance(res, FetchResult)
    assert res.status == 200
    assert len(res.text) > 100
    assert "Standard" in res.text or "Pricing" in res.text


@pytest.mark.live
def test_live_tf_monitor_lifecycle():
    tf = TF()
    # Create monitor
    mon_id, baseline = tf.monitor_create(
        url="https://httpbin.org/html",
        interval_minutes=60,
        name="Pytest Live Monitor",
        purpose="Live monitor test",
        app="time_machine"
    )
    assert mon_id is not None
    assert bool(baseline.get("id"))

    try:
        # Trigger Run Now
        run = tf.monitor_trigger_run(mon_id)
        assert run is not None
        assert bool(run.get("id"))
    finally:
        # Delete monitor
        tf.monitor_delete(mon_id, app="time_machine")


@pytest.mark.live
def test_live_tf_screenshot():
    tf = TF()
    img_bytes = tf.screenshot("https://httpbin.org/html")
    assert isinstance(img_bytes, bytes)
    assert len(img_bytes) > 1000
    # Check JPEG magic header 0xFF 0xD8
    assert img_bytes[:2] == b"\xff\xd8"
