"""
tinycore/events.py
Handles reading and marking monitor webhook events from the Supabase monitor_events table.
Enforces idempotent event processing across Time Machine, TinyVerify, and Site2MCP.
"""

import os
from dataclasses import dataclass

import httpx
from dotenv import dotenv_values

PROCESSED_RUN_CACHE = set()


@dataclass
class MonitorEvent:
    id: str
    received_at: str
    monitor_id: str | None
    run_id: str | None
    payload: dict
    processed: bool = False


def _get_supabase_config():
    sb_url = os.getenv("SUPABASE_URL")
    sb_key = os.getenv("SUPABASE_KEY")
    if not (sb_url and sb_key):
        cfg = dotenv_values(".env")
        sb_url = sb_url or cfg.get("SUPABASE_URL")
        sb_key = sb_key or cfg.get("SUPABASE_KEY")
    return sb_url, sb_key


def fetch_unprocessed_events(limit: int = 50) -> list[MonitorEvent]:
    """Retrieves unprocessed monitor events from Supabase REST table."""
    sb_url, sb_key = _get_supabase_config()
    if not (sb_url and sb_key):
        return []

    url = f"{sb_url}/rest/v1/monitor_events?processed=eq.false&order=received_at.asc&limit={limit}"
    headers = {
        "apikey": sb_key,
        "Authorization": f"Bearer {sb_key}"
    }

    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code == 200:
                rows = resp.json()
                events = []
                for r in rows:
                    events.append(MonitorEvent(
                        id=str(r.get("id")),
                        received_at=r.get("received_at", ""),
                        monitor_id=r.get("monitor_id"),
                        run_id=r.get("run_id"),
                        payload=r.get("payload", {}),
                        processed=bool(r.get("processed", False))
                    ))
                return events
    except Exception:
        pass
    return []


def mark_event_processed(event_id: str) -> bool:
    """Marks a monitor event as processed in Supabase REST table."""
    sb_url, sb_key = _get_supabase_config()
    if not (sb_url and sb_key):
        return False

    url = f"{sb_url}/rest/v1/monitor_events?id=eq.{event_id}"
    headers = {
        "apikey": sb_key,
        "Authorization": f"Bearer {sb_key}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal"
    }

    try:
        with httpx.Client(timeout=10) as client:
            resp = client.patch(url, headers=headers, json={"processed": True})
            return resp.status_code in (200, 204)
    except Exception:
        return False


def is_already_processed(monitor_id: str | None, run_id: str | None) -> bool:
    """Idempotency check to prevent duplicate execution on redelivered webhook runs."""
    if not run_id:
        return False
    key = f"{monitor_id or ''}:{run_id}"
    if key in PROCESSED_RUN_CACHE:
        return True
    PROCESSED_RUN_CACHE.add(key)
    return False
