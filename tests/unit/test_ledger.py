"""
tests/test_ledger.py
Unit tests for MonitorBudget and CostLedger in tinycore.ledger.
"""

import os
import pytest
from tinycore.ledger import (
    MonitorBudget,
    CostLedger,
    QuotaExceeded,
    BudgetExceeded,
    _get_db,
    DB_PATH
)


@pytest.fixture(autouse=True)
def clean_db():
    conn = _get_db()
    with conn:
        conn.execute("UPDATE monitor_allocations SET active_count = 0")
        conn.execute("DELETE FROM cost_ledger")
    yield


def test_monitor_budget_app_allocation():
    # Allocating within quota
    alloc = MonitorBudget.allocate("tiny_verify", 5)
    assert alloc == 5

    status = MonitorBudget.get_status()
    assert status["apps"]["tiny_verify"]["active"] == 5
    assert status["apps"]["tiny_verify"]["quota"] == 10
    assert status["spare"] == 45

    # Allocating beyond quota (10 max, currently 5, requesting 6 -> 11)
    with pytest.raises(QuotaExceeded):
        MonitorBudget.allocate("tiny_verify", 6)

    # Release
    left = MonitorBudget.release("tiny_verify", 2)
    assert left == 3


def test_monitor_budget_global_cap():
    # Max allocation across apps
    MonitorBudget.allocate("time_machine", 30)
    MonitorBudget.allocate("tiny_verify", 10)
    MonitorBudget.allocate("site2mcp", 2)
    
    status = MonitorBudget.get_status()
    assert status["total_active"] == 42
    assert status["spare"] == 8

    # Releasing
    MonitorBudget.release("time_machine", 30)
    assert MonitorBudget.get_status()["apps"]["time_machine"]["active"] == 0


def test_cost_ledger_daily_tracking(monkeypatch):
    monkeypatch.setenv("MAX_DAILY_USD", "1.00")
    assert CostLedger.get_max_daily() == 1.00

    # Initial spend is 0
    assert CostLedger.get_daily_spend() == 0.0

    # Record some calls
    CostLedger.record_call("fetch", 0.00)
    CostLedger.record_call("monitor", 0.005)
    CostLedger.record_call("agent", 0.032)
    assert round(CostLedger.get_daily_spend(), 3) == 0.037

    # Check budget allows small cost
    CostLedger.check_budget(0.01)

    # Budget exceeded check
    with pytest.raises(BudgetExceeded):
        CostLedger.check_budget(1.50)
