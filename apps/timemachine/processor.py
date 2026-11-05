"""
apps/timemachine/processor.py
Event processor for Time Machine:
- Reads unprocessed webhook monitor_events from the store
- Enforces strict idempotency on run_id
- Maps monitor_id -> page
- Runs deterministic change detection (markdown_to_blocks + diff_blocks)
- Writes checks(unchanged | noise | changed | error)
- On material change: captures screenshot, stores new version & diff, classifies via Groq LLM
- Runs in a continuous loop every 60s and on on-demand triggers (/internal/wake)
"""

import json
import logging
import os
import threading
import time
import uuid
from collections import Counter
from datetime import datetime, timezone

from dotenv import dotenv_values

from tinycore.differ import diff_blocks
from tinycore.llm import classify_change
from tinycore.normalize import Block, compute_norm_hash, markdown_to_blocks
from tinycore.store import ScreenshotStore, get_store
from tinycore.tf_client import TF

logger = logging.getLogger("timemachine.processor")


def get_unprocessed_events(store=None) -> list[dict]:
    """Fetches unprocessed events from the storage backend (SQLite or Supabase)."""
    if store is None:
        store = get_store()

    # If store has _conn (SqliteStore), read from monitor_events table
    if hasattr(store, "_conn"):
        conn = store._conn()
        try:
            cur = conn.execute("SELECT * FROM monitor_events WHERE processed = 0 ORDER BY received_at ASC")
            events = []
            for row in cur.fetchall():
                d = dict(row)
                d["processed"] = bool(d["processed"])
                try:
                    d["payload"] = json.loads(d["payload"])
                except Exception:
                    pass
                events.append(d)
            return events
        finally:
            conn.close()

    # Fallback to Supabase REST via tinycore.events
    try:
        from tinycore.events import fetch_unprocessed_events
        return fetch_unprocessed_events()
    except Exception as e:
        logger.warning(f"Failed to fetch unprocessed events from Supabase: {e}")
        return []


def mark_event_processed(event_id: str, store=None):
    """Marks an event as processed in the store."""
    if store is None:
        store = get_store()

    if hasattr(store, "_conn"):
        conn = store._conn()
        try:
            with conn:
                conn.execute("UPDATE monitor_events SET processed = 1 WHERE id = ?", (event_id,))
        finally:
            conn.close()
        return

    try:
        from tinycore.events import mark_event_processed as sb_mark
        sb_mark(event_id)
    except Exception as e:
        logger.warning(f"Failed to mark event processed in Supabase: {e}")


def insert_incoming_event(event_dict: dict, store=None) -> str:
    """Inserts a raw webhook event into the monitor_events table."""
    if store is None:
        store = get_store()

    event_id = event_dict.get("id") or str(uuid.uuid4())
    received_at = event_dict.get("received_at") or datetime.now(timezone.utc).isoformat()
    monitor_id = event_dict.get("monitor_id")
    run_id = event_dict.get("run_id")
    payload = event_dict.get("payload", {})
    payload_str = json.dumps(payload) if not isinstance(payload, str) else payload

    if hasattr(store, "_conn"):
        conn = store._conn()
        try:
            with conn:
                conn.execute("""
                    INSERT INTO monitor_events (id, received_at, monitor_id, run_id, payload, processed)
                    VALUES (?, ?, ?, ?, ?, 0)
                """, (event_id, received_at, monitor_id, run_id, payload_str))
            return event_id
        finally:
            conn.close()
    return event_id


def process_single_event(event: dict, store=None, tf: TF | None = None) -> dict:
    """Processes a single monitor event with full error isolation and idempotency."""
    if store is None:
        store = get_store()
    if tf is None:
        cfg = dotenv_values(".env")
        api_key = cfg.get("TINYFISH_API_KEY")
        tf = TF(api_key=api_key)

    event_id = str(event.get("id", ""))
    payload = event.get("payload", {})
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {}

    # Extract run ID
    run_id = (
        event.get("run_id")
        or payload.get("id")
        or payload.get("run_id")
        or payload.get("data", {}).get("run_id")
    )
    monitor_id = (
        event.get("monitor_id")
        or payload.get("fetch_monitor_id")
        or payload.get("monitor_id")
        or payload.get("data", {}).get("monitor_id")
    )

    # 1. Idempotency on run_id: Check if this run_id was already handled in checks
    if run_id:
        has_run = False
        if hasattr(store, "get_check_by_run_id"):
            has_run = store.get_check_by_run_id(run_id) is not None
        else:
            existing_checks = store.get_checks(limit=500)
            has_run = any(c.get("run_id") == run_id for c in existing_checks)
        if has_run:
            logger.info(f"Event with run_id '{run_id}' already processed. Marking done.")
            mark_event_processed(event_id, store)
            return {"status": "skipped", "reason": "already_processed", "run_id": run_id}

    # 2. Map monitor_id -> page
    all_pages = store.list_pages()
    page = next((p for p in all_pages if p.get("monitor_id") == monitor_id), None)
    if not page:
        logger.warning(f"Unknown monitor_id '{monitor_id}' in event '{event_id}'. Marking processed.")
        mark_event_processed(event_id, store)
        return {"status": "skipped", "reason": "unknown_monitor", "monitor_id": monitor_id}

    page_id = page["id"]
    observed_at = (
        payload.get("created_at")
        or payload.get("timestamp")
        or event.get("received_at")
        or datetime.now(timezone.utc).isoformat()
    )

    # 3. Handle errors in run
    errors = payload.get("errors", [])
    run_status = payload.get("status")
    if errors or run_status == "FAILED":
        logger.warning(f"Run {run_id} failed with errors: {errors}")
        store.add_check({
            "page_id": page_id,
            "observed_at": observed_at,
            "run_id": run_id,
            "outcome": "error",
            "trigger": "scheduled",
            "detail": {"errors": errors, "status": run_status}
        })
        mark_event_processed(event_id, store)
        return {"status": "error", "outcome": "error", "page_id": page_id}

    # 4. Extract markdown text from payload (or fallback)
    results = payload.get("results") or payload.get("data", {}).get("results", [])
    new_text_md = ""
    if results and isinstance(results, list) and len(results) > 0:
        new_text_md = results[0].get("text", "")

    if not new_text_md:
        logger.info(f"No text in payload for {page['url']}; fetching via TinyFish Fetch API...")
        try:
            fetch_res = tf.fetch(page["url"], format="markdown")
            new_text_md = fetch_res.text
        except Exception as e:
            logger.error(f"Fallback fetch failed for {page['url']}: {e}")
            store.add_check({
                "page_id": page_id,
                "observed_at": observed_at,
                "run_id": run_id,
                "outcome": "error",
                "trigger": "scheduled",
                "detail": {"error": f"Failed to retrieve page text: {e}"}
            })
            mark_event_processed(event_id, store)
            return {"status": "error", "outcome": "error", "page_id": page_id}

    new_blocks = markdown_to_blocks(new_text_md, ignore_selectors=page.get("ignore_selectors"))
    new_hash = compute_norm_hash(new_text_md)

    # 5. Diff against previous version
    prev_version = store.get_latest_version(page_id)
    if not prev_version:
        logger.warning(f"No prior version for page {page_id}. Storing baseline v1.")
        # Baseline creation
        v1_data = {
            "page_id": page_id,
            "version_no": 1,
            "observed_at": observed_at,
            "processed_at": datetime.now(timezone.utc).isoformat(),
            "lag_seconds": 0,
            "content_hash": new_hash,
            "blocks": [b.__dict__ if hasattr(b, "__dict__") else b for b in new_blocks],
            "text_md": new_text_md,
            "screenshot_url": None,
            "screenshot_at": None,
            "source_url": page["url"],
            "run_id": run_id,
            "classification": {"type": "baseline", "materiality": 1.0, "summary": "Initial baseline snapshot"},
            "summary": "Initial baseline snapshot"
        }
        store.add_version(v1_data)
        store.add_check({
            "page_id": page_id,
            "observed_at": observed_at,
            "run_id": run_id,
            "outcome": "baseline",
            "trigger": "baseline",
            "detail": {"version_no": 1}
        })
        mark_event_processed(event_id, store)
        return {"status": "success", "outcome": "baseline", "page_id": page_id}

    # Case A: Identical hash -> unchanged
    if prev_version["content_hash"] == new_hash:
        logger.info(f"Page {page['url']} unchanged (identical hash {new_hash[:8]}).")
        store.update_page(page_id, {"last_checked_at": observed_at})
        store.add_check({
            "page_id": page_id,
            "observed_at": observed_at,
            "run_id": run_id,
            "outcome": "unchanged",
            "trigger": "scheduled",
            "detail": {"content_hash": new_hash}
        })
        mark_event_processed(event_id, store)
        return {"status": "success", "outcome": "unchanged", "page_id": page_id}

    # Case B: Compute structural diff
    prev_blocks = [
        Block(**b) if isinstance(b, dict) else b for b in prev_version.get("blocks", [])
    ]
    diff = diff_blocks(prev_blocks, new_blocks)

    if diff.is_empty:
        # Case B1: Noise only (dates, last updated, transient tokens)
        logger.info(f"Page {page['url']} diff normalized to empty (noise only).")
        store.update_page(page_id, {"last_checked_at": observed_at})
        store.add_check({
            "page_id": page_id,
            "observed_at": observed_at,
            "run_id": run_id,
            "outcome": "noise",
            "trigger": "scheduled",
            "detail": {"reason": "Transient dates or metadata", "content_hash": new_hash}
        })
        mark_event_processed(event_id, store)
        return {"status": "success", "outcome": "noise", "page_id": page_id}

    # Case B2: Potential material change detected! Perform 3-observation confirmation (C3)
    logger.info(f"Potential change detected for {page['url']}. Performing confirmation fetches (C3)...")
    wait_s = float(os.getenv("CONFIRMATION_WAIT_SECONDS", "0.0" if os.getenv("PYTEST_CURRENT_TEST") else "90.0"))

    # Observation 1: from monitor payload
    obs1_hash = new_hash
    obs1_text = new_text_md

    # Observation 2: immediate Fetch #1
    obs2_hash = "error"
    obs2_text = ""
    try:
        f1 = tf.fetch(page["url"], format="markdown")
        obs2_text = f1.text
        obs2_hash = compute_norm_hash(obs2_text)
    except Exception as e:
        logger.warning(f"Confirmation fetch 1 failed for {page['url']}: {e}")

    # Wait >=90s before Fetch #2 (configurable for tests via CONFIRMATION_WAIT_SECONDS)
    if wait_s > 0:
        time.sleep(wait_s)

    # Observation 3: Fetch #2
    obs3_hash = "error"
    obs3_text = ""
    try:
        f2 = tf.fetch(page["url"], format="markdown")
        obs3_text = f2.text
        obs3_hash = compute_norm_hash(obs3_text)
    except Exception as e:
        logger.warning(f"Confirmation fetch 2 failed for {page['url']}: {e}")

    # Calculate agreement among observations
    obs_hashes = [obs1_hash, obs2_hash, obs3_hash]
    counts = Counter(obs_hashes)
    agreed_hash, agree_count = counts.most_common(1)[0]

    prev_hash = prev_version.get("content_hash")
    is_confirmed = (agree_count >= 2) and (agreed_hash != prev_hash) and (agreed_hash != "error")

    if not is_confirmed:
        reason = "reverted_to_previous" if agreed_hash == prev_hash else "flapping_or_unconfirmed"
        logger.info(f"Change unconfirmed for {page['url']}: reason={reason}, agree_count={agree_count}/3")
        store.update_page(page_id, {"last_checked_at": observed_at})
        store.add_check({
            "page_id": page_id,
            "observed_at": observed_at,
            "run_id": run_id,
            "outcome": "unconfirmed",
            "trigger": "scheduled",
            "detail": {
                "reason": reason,
                "agree_count": agree_count,
                "observations": [
                    {"obs": 1, "hash": obs1_hash},
                    {"obs": 2, "hash": obs2_hash},
                    {"obs": 3, "hash": obs3_hash}
                ],
                "prev_hash": prev_hash
            }
        })
        mark_event_processed(event_id, store)
        return {
            "status": "success",
            "outcome": "unconfirmed",
            "page_id": page_id,
            "reason": reason
        }

    # If confirmed, determine which observation text matches agreed_hash
    if agreed_hash == obs1_hash:
        new_text_md = obs1_text
    elif agreed_hash == obs2_hash:
        new_text_md = obs2_text
    else:
        new_text_md = obs3_text

    new_blocks = markdown_to_blocks(new_text_md, ignore_selectors=page.get("ignore_selectors"))
    new_hash = agreed_hash
    diff = diff_blocks(prev_blocks, new_blocks)

    now_utc = datetime.now(timezone.utc)
    processed_at = now_utc.isoformat()
    try:
        obs_dt = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        lag_seconds = int((now_utc - obs_dt).total_seconds())
    except Exception:
        lag_seconds = 0

    # Screenshot immediately upon confirmed change
    screenshot_url = None
    screenshot_at = processed_at
    try:
        shot_bytes = tf.screenshot(page["url"])
        shot_key = f"tm_{uuid.uuid4().hex[:12]}.jpg"
        screenshot_url = ScreenshotStore.put(shot_bytes, shot_key)
        screenshot_at = datetime.now(timezone.utc).isoformat()
    except Exception as e:
        logger.warning(f"Screenshot capture failed during change processing: {e}")

    new_version_no = prev_version["version_no"] + 1

    # Store version row
    new_ver = store.add_version({
        "page_id": page_id,
        "version_no": new_version_no,
        "observed_at": observed_at,
        "processed_at": processed_at,
        "lag_seconds": max(0, lag_seconds),
        "content_hash": new_hash,
        "blocks": [b.__dict__ if hasattr(b, "__dict__") else b for b in new_blocks],
        "text_md": new_text_md,
        "screenshot_url": screenshot_url,
        "screenshot_at": screenshot_at,
        "source_url": page["url"],
        "run_id": run_id,
        "classification": None,
        "summary": None
    })

    # Store diffs row
    diff_dict = diff.to_dict()
    store.add_diff({
        "page_id": page_id,
        "from_version": prev_version["version_no"],
        "to_version": new_version_no,
        "diff": diff_dict
    })

    # Classify via Groq LLM (Rule: Groq only labels, Python already decided change)
    baseline_blocks_count = len(prev_blocks)
    classification = None
    try:
        classification = classify_change(diff_dict, baseline_blocks_count=baseline_blocks_count)
        if classification and hasattr(store, "update_version"):
            summary_txt = classification.get("summary")
            store.update_version(new_ver["id"], {
                "classification": classification,
                "summary": summary_txt
            })
    except Exception as e:
        logger.warning(f"LLM classification failed (storing null): {e}")

    # Store check with review_status='unreviewed' (C5)
    store.add_check({
        "page_id": page_id,
        "observed_at": observed_at,
        "run_id": run_id,
        "outcome": "changed",
        "trigger": "scheduled",
        "detail": {
            "version_no": new_version_no,
            "version_id": new_ver.get("id"),
            "added_count": len(diff.added),
            "removed_count": len(diff.removed),
            "changed_count": len(diff.changed),
            "classification": classification,
            "screenshot_url": screenshot_url,
            "confirmed": True,
            "agree_count": agree_count,
            "review_status": "unreviewed",
            "review_note": None
        }
    })

    store.update_page(page_id, {"last_checked_at": observed_at})
    mark_event_processed(event_id, store)
    logger.info(f"Successfully processed confirmed change for {page['url']}: Version {new_version_no} created.")
    return {
        "status": "success",
        "outcome": "changed",
        "page_id": page_id,
        "version_no": new_version_no,
        "classification": classification
    }


def process_pending_events(store=None, tf=None) -> int:
    """Processes all unprocessed monitor events."""
    if store is None:
        store = get_store()
    events = get_unprocessed_events(store)
    if not events:
        return 0

    processed_count = 0
    for ev in events:
        try:
            process_single_event(ev, store=store, tf=tf)
            processed_count += 1
        except Exception as e:
            logger.error(f"Error processing event {ev.get('id')}: {e}", exc_info=True)
            # Never let one error stop the processor loop
    return processed_count


class ProcessorWorker:
    """Background worker executing the processor loop every 60s."""
    def __init__(self, interval_seconds: int = 60, store=None, tf=None):
        self.interval = interval_seconds
        self.store = store or get_store()
        self.tf = tf
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_run_at: str | None = None
        self.last_run_epoch: float = time.time()
        self.events_processed_total: int = 0

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="TM-ProcessorWorker")
        self._thread.start()
        logger.info("Time Machine Processor Worker started.")

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Time Machine Processor Worker stopped.")

    def wake(self) -> int:
        """Immediately triggers an on-demand event drain."""
        logger.info("Processor woke up on on-demand trigger.")
        cnt = process_pending_events(store=self.store, tf=self.tf)
        self.events_processed_total += cnt
        self.last_run_at = datetime.now(timezone.utc).isoformat()
        self.last_run_epoch = time.time()
        return cnt

    def _run_loop(self):
        while not self._stop_event.is_set():
            try:
                cnt = process_pending_events(store=self.store, tf=self.tf)
                self.events_processed_total += cnt
                self.last_run_at = datetime.now(timezone.utc).isoformat()
                self.last_run_epoch = time.time()
            except Exception as e:
                logger.error(f"Error in processor loop: {e}", exc_info=True)
            self._stop_event.wait(timeout=self.interval)


# Global singleton worker instance
processor_worker = ProcessorWorker()
