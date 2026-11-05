"""
tinycore/ledger.py
Enforces MonitorBudget (hard cap 50, per-app quotas) and CostLedger (daily spend tracking and limits).
Persists in SQLite with WAL mode.
"""

import os
import sqlite3
from datetime import datetime, timezone

DB_PATH = os.path.join("data", "app.db")
MAX_DAILY_USD_DEFAULT = 2.50

APP_QUOTAS = {
    "time_machine": 30,
    "tiny_verify": 10,
    "site2mcp": 2
}
GLOBAL_MONITOR_CAP = 50

# Rate estimates per unit
RATES = {
    "fetch": 0.00,
    "search": 0.00,
    "monitor": 0.005,
    "monitor_create": 0.005,
    "monitor_run": 0.005,
    "agent_step": 0.016,
    "agent_run_default": 0.064,
    "browser_minute": 0.002,
    "browser_screenshot": 0.0004,
    "screenshot": 0.0004
}


class BudgetExceeded(Exception):
    """Raised when daily spend exceeds MAX_DAILY_USD."""
    pass


class QuotaExceeded(Exception):
    """Raised when monitor allocation exceeds per-app quota or global cap."""
    pass


def _get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS monitor_allocations (
                app TEXT PRIMARY KEY,
                active_count INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cost_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                date_str TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                cost REAL NOT NULL,
                details TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ledger_date ON cost_ledger(date_str)")
        for app in APP_QUOTAS:
            conn.execute("INSERT OR IGNORE INTO monitor_allocations (app, active_count) VALUES (?, 0)", (app,))
    return conn


class MonitorBudget:
    @staticmethod
    def allocate(app: str, count: int = 1) -> int:
        if app not in APP_QUOTAS:
            raise ValueError(f"Unknown app '{app}'. Supported: {list(APP_QUOTAS.keys())}")

        conn = _get_db()
        try:
            with conn:
                cur = conn.execute("SELECT SUM(active_count) FROM monitor_allocations")
                total_active = cur.fetchone()[0] or 0
                if total_active + count > GLOBAL_MONITOR_CAP:
                    raise QuotaExceeded(f"Global monitor cap ({GLOBAL_MONITOR_CAP}) exceeded. Total active: {total_active}, requested: {count}")

                cur = conn.execute("SELECT active_count FROM monitor_allocations WHERE app = ?", (app,))
                row = cur.fetchone()
                current_app_count = row["active_count"] if row else 0
                quota = APP_QUOTAS[app]
                if current_app_count + count > quota:
                    raise QuotaExceeded(f"App '{app}' quota ({quota}) exceeded. Currently active: {current_app_count}, requested: {count}")

                new_count = current_app_count + count
                conn.execute("UPDATE monitor_allocations SET active_count = ? WHERE app = ?", (new_count, app))
                return new_count
        finally:
            conn.close()

    @staticmethod
    def release(app: str, count: int = 1) -> int:
        if app not in APP_QUOTAS:
            return 0
        conn = _get_db()
        try:
            with conn:
                cur = conn.execute("SELECT active_count FROM monitor_allocations WHERE app = ?", (app,))
                row = cur.fetchone()
                current_count = row["active_count"] if row else 0
                new_count = max(0, current_count - count)
                conn.execute("UPDATE monitor_allocations SET active_count = ? WHERE app = ?", (new_count, app))
                return new_count
        finally:
            conn.close()

    @staticmethod
    def get_status() -> dict:
        conn = _get_db()
        try:
            cur = conn.execute("SELECT app, active_count FROM monitor_allocations")
            apps = {}
            total = 0
            for row in cur.fetchall():
                app = row["app"]
                cnt = row["active_count"]
                apps[app] = {"active": cnt, "quota": APP_QUOTAS.get(app, 0)}
                total += cnt
            return {
                "total_active": total,
                "global_cap": GLOBAL_MONITOR_CAP,
                "spare": GLOBAL_MONITOR_CAP - total,
                "apps": apps
            }
        finally:
            conn.close()


class CostLedger:
    @staticmethod
    def get_max_daily() -> float:
        val = os.getenv("MAX_DAILY_USD")
        if val:
            try:
                return float(val)
            except ValueError:
                pass
        return MAX_DAILY_USD_DEFAULT

    @classmethod
    def get_daily_spend(cls, date_str: str | None = None) -> float:
        if not date_str:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        conn = _get_db()
        try:
            cur = conn.execute("SELECT SUM(cost) FROM cost_ledger WHERE date_str = ?", (date_str,))
            return float(cur.fetchone()[0] or 0.0)
        finally:
            conn.close()

    @classmethod
    def get_daily_fetch_count(cls, date_str: str | None = None) -> int:
        if not date_str:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        conn = _get_db()
        try:
            cur = conn.execute("SELECT COUNT(*) FROM cost_ledger WHERE date_str = ? AND endpoint = 'fetch'", (date_str,))
            return int(cur.fetchone()[0] or 0)
        finally:
            conn.close()

    @classmethod
    def check_budget(cls, estimated_cost: float, endpoint: str = ""):
        max_daily = cls.get_max_daily()
        current_daily = cls.get_daily_spend()
        if current_daily + estimated_cost > max_daily:
            raise BudgetExceeded(
                f"Daily spend limit exceeded: current ${current_daily:.4f} + ${estimated_cost:.4f} > limit ${max_daily:.2f}"
            )
        if endpoint == "fetch":
            fetches = cls.get_daily_fetch_count()
            if fetches >= 1000:
                raise BudgetExceeded(f"Daily TinyFish fetch limit reached: {fetches}/1000 fetches today.")

    @classmethod
    def record_call(cls, endpoint: str, cost: float, details: str = ""):
        now = datetime.now(timezone.utc)
        ts = now.isoformat()
        date_str = now.strftime("%Y-%m-%d")
        conn = _get_db()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO cost_ledger (ts, date_str, endpoint, cost, details) VALUES (?, ?, ?, ?, ?)",
                    (ts, date_str, endpoint, cost, details)
                )
        finally:
            conn.close()
