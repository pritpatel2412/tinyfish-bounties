"""
apps/timemachine/watchdog.py
Hourly in-process watchdog for Time Machine operations:
- Checks TinyFish account for any paused monitors and automatically resumes them
- Verifies drift between local pages database and live TinyFish monitors
- Alerts when TinyFish wallet balance drops below $8.00 threshold
- Runs continuously in background every hour or on-demand
"""

import logging
import threading
from datetime import datetime, timezone

from dotenv import dotenv_values

from tinycore.store import get_store
from tinycore.tf_client import TF

logger = logging.getLogger("timemachine.watchdog")

MIN_WALLET_BALANCE_ALERT_USD = 8.00


def run_watchdog_check(store=None, tf: TF | None = None) -> dict:
    """Executes a complete single-pass operational health check."""
    if store is None:
        store = get_store()
    if tf is None:
        cfg = dotenv_values(".env")
        api_key = cfg.get("TINYFISH_API_KEY")
        tf = TF(api_key=api_key)

    check_result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "resumed_monitors": [],
        "drift_pages": [],
        "wallet_balance_usd": None,
        "wallet_alert": False,
        "errors": []
    }

    # 1. Check wallet balance
    try:
        w_data = tf.wallet_get()
        bal = None
        if isinstance(w_data, dict):
            bal_str = w_data.get("available_balance")
            if bal_str is not None:
                bal = float(bal_str)
        elif isinstance(w_data, (int, float)):
            bal = float(w_data)

        check_result["wallet_balance_usd"] = bal
        if bal is not None and bal < MIN_WALLET_BALANCE_ALERT_USD:
            check_result["wallet_alert"] = True
            logger.error(
                f"[WATCHDOG CRITICAL] TinyFish wallet balance is low: ${bal:.2f}! "
                f"Minimum recommended threshold is ${MIN_WALLET_BALANCE_ALERT_USD:.2f}."
            )
        elif bal is not None:
            logger.info(f"[WATCHDOG] TinyFish wallet balance is healthy: ${bal:.2f}")
    except Exception as e:
        err_msg = f"Failed to retrieve wallet balance: {e}"
        logger.warning(f"[WATCHDOG] {err_msg}")
        check_result["errors"].append(err_msg)

    # 2. Check monitors list and resume paused monitors
    live_monitors: list[dict] = []
    try:
        live_monitors = tf.monitors_list()
        for m in live_monitors:
            m_id = m.get("id")
            m_status = str(m.get("status", "")).upper()
            if m_status == "PAUSED" and m_id:
                logger.warning(f"[WATCHDOG] Found paused monitor '{m_id}' ({m.get('name')}). Resuming...")
                try:
                    tf.monitor_resume(m_id)
                    check_result["resumed_monitors"].append(m_id)
                    logger.info(f"[WATCHDOG] Successfully resumed monitor '{m_id}'")
                except Exception as ex:
                    logger.error(f"[WATCHDOG] Failed to resume monitor '{m_id}': {ex}")
                    check_result["errors"].append(f"Failed to resume monitor {m_id}: {ex}")
    except Exception as e:
        err_msg = f"Failed to list monitors: {e}"
        logger.warning(f"[WATCHDOG] {err_msg}")
        check_result["errors"].append(err_msg)

    # 3. Check drift between pages in DB and live monitors
    try:
        active_pages = [p for p in store.list_pages() if p.get("active")]
        live_monitor_ids = {m.get("id") for m in live_monitors if m.get("id")}

        for p in active_pages:
            p_mon_id = p.get("monitor_id")
            if not p_mon_id or (live_monitors and p_mon_id not in live_monitor_ids):
                logger.warning(
                    f"[WATCHDOG DRIFT] Active page '{p['url']}' has missing monitor ID '{p_mon_id}'"
                )
                check_result["drift_pages"].append({
                    "page_id": p["id"],
                    "url": p["url"],
                    "monitor_id": p_mon_id
                })
    except Exception as e:
        err_msg = f"Failed checking page drift: {e}"
        logger.warning(f"[WATCHDOG] {err_msg}")
        check_result["errors"].append(err_msg)

    return check_result


class WatchdogWorker:
    """Background thread running the watchdog check every 60 minutes."""
    def __init__(self, interval_seconds: int = 3600, store=None, tf: TF | None = None):
        self.interval = interval_seconds
        self.store = store
        self.tf = tf
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_check_at: str | None = None
        self.last_result: dict | None = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="TM-WatchdogWorker")
        self._thread.start()
        logger.info("Time Machine Watchdog Worker started.")

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Time Machine Watchdog Worker stopped.")

    def run_now(self) -> dict:
        result = run_watchdog_check(store=self.store, tf=self.tf)
        self.last_check_at = result["timestamp"]
        self.last_result = result
        return result

    def _run_loop(self):
        while not self._stop_event.is_set():
            try:
                self.run_now()
            except Exception as e:
                logger.error(f"[WATCHDOG ERROR] Unhandled exception in watchdog: {e}", exc_info=True)
            self._stop_event.wait(timeout=self.interval)


# Global singleton watchdog instance
watchdog_worker = WatchdogWorker()
