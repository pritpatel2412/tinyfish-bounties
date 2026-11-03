"""
tests/test_agent_cost.py
Unit tests verifying agent_run cost calculation (num_of_steps * 0.016)
and recording to CostLedger and tf_calls.jsonl.
"""

import os
import json
import pytest
from unittest.mock import MagicMock, patch
from tinycore.tf_client import TF
from tinycore.ledger import CostLedger, RATES
from tinycore.api_common import get_endpoint_usage_summary


def test_agent_run_cost_recording(tmp_path, monkeypatch):
    test_db = str(tmp_path / "test_app.db")
    test_calls = str(tmp_path / "test_calls.jsonl")

    # Point DB_PATH and CALLS_LOG to test fixtures
    monkeypatch.setattr("tinycore.ledger.DB_PATH", test_db)
    monkeypatch.setattr("tinycore.tf_client.CALLS_LOG", test_calls)
    monkeypatch.setattr("tinycore.api_common.CALLS_LOG", test_calls)

    client = TF(api_key="mock-key")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "run_id": "test-run-456",
        "status": "COMPLETED",
        "num_of_steps": 5,
        "result": {"status": "ACTIVE"}
    }

    with patch.object(client, "_execute_http", return_value=mock_resp):
        res = client.agent_run("https://example.com", "find info", {"type": "object"})

    assert res.run_id == "test-run-456"
    assert res.num_steps == 5
    expected_cost = round(5 * RATES["agent_step"], 4)
    assert expected_cost == 0.08

    # 1. Verify CostLedger recorded 0.08
    assert CostLedger.get_daily_spend() == pytest.approx(0.08, 0.0001)

    # 2. Verify tf_calls.jsonl recorded cost_usd: 0.08
    assert os.path.exists(test_calls)
    with open(test_calls, "r", encoding="utf-8") as f:
        lines = [json.loads(line) for line in f if line.strip()]
    assert len(lines) == 1
    call = lines[0]
    assert call["endpoint"] == "agent_run"
    assert call["run_id"] == "test-run-456"
    assert call["cost_usd"] == pytest.approx(0.08, 0.0001)

    # 3. Verify get_endpoint_usage_summary aggregates the cost
    summary = get_endpoint_usage_summary()
    assert summary["total_calls"] == 1
    assert summary["total_cost_usd"] == pytest.approx(0.08, 0.0001)
    assert "agent_run" in summary["endpoints"]
    assert summary["endpoints"]["agent_run"]["total_cost_usd"] == pytest.approx(0.08, 0.0001)
