"""
apps/timemachine/capture.py
Handles adding and capturing baseline snapshots for pages in Time Machine:
- Enforces live monitor quota (<= 30 for Time Machine)
- Creates TinyFish Monitor (type='fetch', format='markdown')
- Stores baseline version 1 with observed_at, content_hash, and blocks
- Captures initial screenshot via Browser CDP and stores to ScreenshotStore
- Logs baseline check outcome
"""

import os
import uuid
import logging
from datetime import datetime, timezone
from dotenv import dotenv_values

from tinycore.tf_client import TF, TFError
from tinycore.ledger import QuotaExceeded
from tinycore.normalize import markdown_to_blocks, compute_norm_hash
from tinycore.store import get_store, ScreenshotStore

logger = logging.getLogger("timemachine.capture")

APP_NAME = "time_machine"
MAX_APP_MONITORS = 30


def add_page(
    url: str,
    title: str | None = None,
    category: str | None = None,
    cron: str | None = None,
    interval_label: str | None = None,
    webhook_url: str | None = None,
    store=None,
    tf: TF | None = None
) -> dict:
    """Adds a page to Time Machine, sets up a live monitor, and captures version 1 baseline."""
    if store is None:
        store = get_store()
    if tf is None:
        cfg = dotenv_values(".env")
        api_key = cfg.get("TINYFISH_API_KEY")
        tf = TF(api_key=api_key)

    # 1. Idempotency check: Return existing page if already registered
    existing = store.get_page_by_url(url)
    if existing:
        logger.info(f"Page already exists in store: {url}")
        v1 = store.get_version(existing["id"], 1)
        return {"page": existing, "version": v1, "is_new": False}

    # 2. Live monitor count check against account cap
    try:
        monitors = tf.monitors_list()
        active_monitors = [m for m in monitors if m.get("status") == "active"]
        # Count Time Machine monitors
        tm_monitors = [m for m in active_monitors if m.get("name", "").startswith("TM:") or m.get("name", "").startswith("TimeMachine")]
        if len(tm_monitors) >= MAX_APP_MONITORS:
            raise QuotaExceeded(
                f"Time Machine monitor quota ({MAX_APP_MONITORS}) reached (currently active: {len(tm_monitors)})."
            )
        if len(active_monitors) >= 50:
            raise QuotaExceeded(f"Global account monitor cap (50) reached (currently active: {len(active_monitors)}).")
    except QuotaExceeded:
        raise
    except Exception as e:
        logger.warning(f"Could not verify live monitor count: {e}")

    # 3. Schedule cron calculation
    if not cron:
        minute = abs(hash(url)) % 30
        cron = f"{minute} */6 * * *"
        if not interval_label:
            interval_label = "6h"

    if webhook_url is None:
        webhook_url = os.getenv("WEBHOOK_URL")

    # 4. Create monitor with TinyFish
    mon_name = f"TM: {title or url[:40]}"
    logger.info(f"Creating fetch monitor for {url} with cron '{cron}'...")
    try:
        mon_res = tf.monitor_create(
            type="fetch",
            config={"url": url, "format": "markdown"},
            schedule_cron=cron,
            name=mon_name,
            webhook_url=webhook_url,
            app=APP_NAME
        )
    except Exception as e:
        logger.error(f"Failed to create TinyFish monitor for {url}: {e}")
        raise TFError(f"Monitor creation failed: {e}")

    monitor_id = mon_res.monitor_id
    baseline_run = mon_res.run or {}
    run_id = baseline_run.get("id")

    # 5. Extract baseline content from run results
    results = baseline_run.get("results", [])
    text_md = ""
    if results and isinstance(results, list):
        text_md = results[0].get("text", "")

    if not text_md:
        logger.info(f"Baseline run contained empty text, performing fallback fetch for {url}...")
        try:
            fetch_res = tf.fetch(url, format="markdown")
            text_md = fetch_res.text
        except Exception as e:
            logger.warning(f"Fallback fetch failed: {e}")
            text_md = ""

    observed_at = baseline_run.get("created_at") or datetime.now(timezone.utc).isoformat()
    blocks = markdown_to_blocks(text_md)
    content_hash = compute_norm_hash(text_md)

    # 6. Capture screenshot via Browser CDP
    screenshot_url = None
    screenshot_at = None
    try:
        logger.info(f"Capturing baseline screenshot for {url}...")
        shot_bytes = tf.screenshot(url)
        screenshot_at = datetime.now(timezone.utc).isoformat()
        shot_key = f"tm_{uuid.uuid4().hex[:12]}.jpg"
        screenshot_url = ScreenshotStore.put(shot_bytes, shot_key)
        logger.info(f"Screenshot captured: {screenshot_url}")
    except Exception as e:
        logger.warning(f"Screenshot capture failed for {url} (will proceed without): {e}")

    # 7. Store page row
    page_data = {
        "url": url,
        "title": title or url,
        "category": category or "general",
        "monitor_id": monitor_id,
        "schedule_cron": cron,
        "interval_label": interval_label or "6h",
        "added_at": observed_at,
        "history_starts_at": observed_at,
        "active": True,
        "status": "active",
        "last_checked_at": observed_at
    }
    page_row = store.add_page(page_data)
    page_id = page_row["id"]

    # 8. Store version 1 row
    version_data = {
        "page_id": page_id,
        "version_no": 1,
        "observed_at": observed_at,
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "lag_seconds": 0,
        "content_hash": content_hash,
        "blocks": [b.__dict__ if hasattr(b, "__dict__") else b for b in blocks],
        "text_md": text_md,
        "screenshot_url": screenshot_url,
        "screenshot_at": screenshot_at,
        "source_url": url,
        "run_id": run_id,
        "classification": {"type": "baseline", "materiality": 1.0, "summary": "Initial baseline snapshot"},
        "summary": "Initial baseline snapshot"
    }
    version_row = store.add_version(version_data)

    # 9. Store check row
    store.add_check({
        "page_id": page_id,
        "observed_at": observed_at,
        "run_id": run_id,
        "outcome": "baseline",
        "detail": {
            "version_no": 1,
            "blocks_count": len(blocks),
            "content_hash": content_hash,
            "screenshot_url": screenshot_url
        }
    })

    return {"page": page_row, "version": version_row, "is_new": True}
