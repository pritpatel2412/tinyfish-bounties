"""
apps/timemachine/api.py
FastAPI REST API for Time Machine.
Provides complete history, as-of point-in-time reconstruction, structured diffs,
audit logs, webhook ingestion, and live export for judges.
"""

import os
import re
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Request, Query, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, HttpUrl

from tinycore.api_common import create_app
from tinycore.store import get_store
from tinycore.normalize import Block
from tinycore.differ import diff_blocks
from apps.timemachine.capture import add_page
from apps.timemachine.processor import (
    processor_worker,
    insert_incoming_event
)

logger = logging.getLogger("timemachine.api")


@asynccontextmanager
async def tm_lifespan(app: FastAPI):
    # Startup: Launch background processor worker
    processor_worker.start()
    logger.info("Time Machine API initialized; processor worker started.")
    yield
    # Shutdown
    processor_worker.stop()
    logger.info("Time Machine API shutdown; processor worker stopped.")


app = create_app(
    title="Time Machine API",
    version="1.0.0",
    lifespan=tm_lifespan
)


# --- Request Models ---

class PageCreateRequest(BaseModel):
    url: str
    title: str | None = None
    category: str | None = "general"
    cron: str | None = None


# --- Helper Utilities ---

def json_error(code: str, message: str, status_code: int = 400, extra: dict | None = None) -> JSONResponse:
    err_body = {
        "error": {
            "code": code,
            "message": message,
            **(extra or {})
        }
    }
    return JSONResponse(status_code=status_code, content=err_body)


def parse_date_to_iso(date_str: str) -> str:
    """Normalizes YYYY-MM-DD or ISO-8601 strings to comparable ISO-8601 strings."""
    date_str = date_str.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
        date_str += "T23:59:59Z"
    elif not date_str.endswith("Z") and not ("+" in date_str[10:] or "-" in date_str[10:]):
        date_str += "Z"
    return date_str


# --- Routes ---

@app.post("/pages", status_code=status.HTTP_201_CREATED)
def create_page_endpoint(req: PageCreateRequest):
    """Adds a page for continuous monitoring with immediate baseline capture."""
    store = get_store()
    try:
        res = add_page(
            url=str(req.url),
            title=req.title,
            category=req.category,
            cron=req.cron,
            store=store
        )
        return res
    except ValueError as e:
        return json_error("BAD_REQUEST", str(e), status_code=400)
    except Exception as e:
        logger.error(f"Failed to add page {req.url}: {e}", exc_info=True)
        return json_error("CAPTURE_FAILED", f"Failed to capture page: {e}", status_code=500)


@app.get("/pages")
def list_pages_endpoint(
    category: str | None = Query(None, description="Filter by category"),
    active: bool | None = Query(None, description="Filter by active status")
):
    """Lists all tracked pages with live monitoring status and version counts."""
    store = get_store()
    pages = store.list_pages()

    result = []
    for p in pages:
        if category and p.get("category") != category:
            continue
        if active is not None and p.get("active") != active:
            continue

        versions = store.get_versions(p["id"], limit=1000)
        p_copy = dict(p)
        p_copy["version_count"] = len(versions)
        result.append(p_copy)

    return result


@app.get("/pages/{page_id}")
def get_page_endpoint(page_id: str):
    """Returns details for a tracked page including latest version metadata."""
    store = get_store()
    page = store.get_page(page_id)
    if not page:
        return json_error("PAGE_NOT_FOUND", f"Page with ID '{page_id}' not found", status_code=404)

    latest = store.get_latest_version(page_id)
    versions = store.get_versions(page_id, limit=1000)
    page_data = dict(page)
    page_data["version_count"] = len(versions)
    page_data["latest_version"] = latest
    return page_data


@app.get("/pages/{page_id}/timeline")
def get_page_timeline(page_id: str):
    """Returns chronological timeline of versions descending."""
    store = get_store()
    page = store.get_page(page_id)
    if not page:
        return json_error("PAGE_NOT_FOUND", f"Page with ID '{page_id}' not found", status_code=404)

    versions = store.get_versions(page_id, limit=500)
    # Sort descending
    versions_sorted = sorted(versions, key=lambda v: v["version_no"], reverse=True)

    timeline = []
    for v in versions_sorted:
        timeline.append({
            "version_no": v["version_no"],
            "observed_at": v["observed_at"],
            "processed_at": v["processed_at"],
            "content_hash": v["content_hash"],
            "screenshot_url": v.get("screenshot_url"),
            "source_url": v["source_url"],
            "summary": v.get("summary"),
            "classification": v.get("classification")
        })
    return {
        "page_id": page_id,
        "url": page["url"],
        "title": page.get("title"),
        "total_versions": len(timeline),
        "timeline": timeline
    }


@app.get("/pages/{page_id}/asof")
def get_page_asof(
    page_id: str,
    date: str = Query(..., description="Target point-in-time (YYYY-MM-DD or ISO-8601)")
):
    """
    Returns the version active as-of the requested date.
    Strictly verifies history_starts_at; returns BEFORE_HISTORY if queried prior to capture.
    """
    store = get_store()
    page = store.get_page(page_id)
    if not page:
        return json_error("PAGE_NOT_FOUND", f"Page with ID '{page_id}' not found", status_code=404)

    target_iso = parse_date_to_iso(date)
    history_starts_at = page.get("history_starts_at") or page.get("added_at")

    if history_starts_at and target_iso < history_starts_at:
        return json_error(
            "BEFORE_HISTORY",
            f"Requested date '{date}' is before monitoring started for this page ({history_starts_at})",
            status_code=404,
            extra={"history_starts_at": history_starts_at, "url": page["url"]}
        )

    ver = store.get_version_asof(page_id, target_iso)
    if not ver:
        return json_error(
            "NO_VERSION_AVAILABLE",
            f"No version found on or before '{date}'",
            status_code=404,
            extra={"history_starts_at": history_starts_at}
        )

    return ver


@app.get("/pages/{page_id}/diff")
def get_page_diff(
    page_id: str,
    from_val: str = Query(..., alias="from", description="From version number or date"),
    to_val: str = Query(..., alias="to", description="To version number or date")
):
    """
    Returns the structured diff between any two versions or dates.
    If versions are consecutive, retrieves cached diff; otherwise computes dynamically.
    """
    store = get_store()
    page = store.get_page(page_id)
    if not page:
        return json_error("PAGE_NOT_FOUND", f"Page with ID '{page_id}' not found", status_code=404)

    def resolve_version_no(val: str) -> int:
        try:
            return int(val)
        except ValueError:
            target_iso = parse_date_to_iso(val)
            hist = page.get("history_starts_at") or page.get("added_at")
            if hist and target_iso < hist:
                raise ValueError(f"Date '{val}' is before history starts ({hist})")
            v = store.get_version_asof(page_id, target_iso)
            if not v:
                raise ValueError(f"No version found for date '{val}'")
            return v["version_no"]

    try:
        from_v = resolve_version_no(from_val)
        to_v = resolve_version_no(to_val)
    except ValueError as e:
        return json_error("INVALID_VERSION_SPEC", str(e), status_code=404)

    if from_v == to_v:
        return {
            "page_id": page_id,
            "from_version": from_v,
            "to_version": to_v,
            "diff": {
                "added": [],
                "removed": [],
                "changed": [],
                "stats": {"added": 0, "removed": 0, "changed": 0, "numeric_deltas": 0},
                "is_empty": True
            }
        }

    # Order correctly
    v_start, v_end = min(from_v, to_v), max(from_v, to_v)

    # Check cached diff
    diff_rec = store.get_diff(page_id, v_start, v_end)
    if diff_rec and "diff" in diff_rec:
        return {
            "page_id": page_id,
            "from_version": v_start,
            "to_version": v_end,
            "diff": diff_rec["diff"]
        }

    # Dynamic calculation
    v_from_data = store.get_version(page_id, v_start)
    v_to_data = store.get_version(page_id, v_end)
    if not v_from_data or not v_to_data:
        return json_error("VERSION_NOT_FOUND", f"Version {v_start} or {v_end} does not exist", status_code=404)

    blocks_from = [Block(**b) if isinstance(b, dict) else b for b in v_from_data.get("blocks", [])]
    blocks_to = [Block(**b) if isinstance(b, dict) else b for b in v_to_data.get("blocks", [])]
    calculated_diff = diff_blocks(blocks_from, blocks_to)

    return {
        "page_id": page_id,
        "from_version": v_start,
        "to_version": v_end,
        "diff": calculated_diff.to_dict()
    }


@app.get("/pages/{page_id}/versions/{version_no}")
def get_page_version_endpoint(page_id: str, version_no: int):
    """Returns full content and metadata for a specific version number."""
    store = get_store()
    page = store.get_page(page_id)
    if not page:
        return json_error("PAGE_NOT_FOUND", f"Page with ID '{page_id}' not found", status_code=404)

    ver = store.get_version(page_id, version_no)
    if not ver:
        return json_error("VERSION_NOT_FOUND", f"Version {version_no} not found for page '{page_id}'", status_code=404)

    return ver


@app.get("/changes")
def list_changes_feed(
    since: str | None = Query(None, description="Filter changes observed since ISO timestamp"),
    category: str | None = Query(None, description="Filter by page category"),
    min_materiality: float | None = Query(None, description="Minimum materiality score (0.0 - 1.0)")
):
    """Global feed of real detected changes across all monitored pages."""
    store = get_store()
    all_pages = {p["id"]: p for p in store.list_pages()}
    all_checks = store.get_checks(limit=500)

    changes = []
    for chk in all_checks:
        if chk.get("outcome") != "changed":
            continue

        p_id = chk["page_id"]
        page = all_pages.get(p_id)
        if not page:
            continue

        if category and page.get("category") != category:
            continue

        obs_at = chk["observed_at"]
        if since and obs_at < since:
            continue

        detail = chk.get("detail", {})
        classification = detail.get("classification") or {}
        materiality = classification.get("materiality", 0.5)

        if min_materiality is not None and materiality < min_materiality:
            continue

        changes.append({
            "page_id": p_id,
            "url": page["url"],
            "title": page.get("title"),
            "category": page.get("category"),
            "version_no": detail.get("version_no"),
            "observed_at": obs_at,
            "summary": classification.get("summary") or "Content change detected",
            "classification": classification,
            "screenshot_url": detail.get("screenshot_url")
        })

    return {
        "total_changes": len(changes),
        "changes": changes
    }


@app.get("/logs")
def public_logs_feed(limit: int = Query(100, ge=1, le=500, description="Number of log rows to return")):
    """Public audit feed of monitoring checks proving continuous live operation."""
    store = get_store()
    all_pages = {p["id"]: p for p in store.list_pages()}
    checks = store.get_checks(limit=limit)

    logs = []
    for chk in checks:
        page = all_pages.get(chk["page_id"], {})
        logs.append({
            "id": chk["id"],
            "page_id": chk["page_id"],
            "url": page.get("url"),
            "title": page.get("title"),
            "category": page.get("category"),
            "run_id": chk.get("run_id"),
            "outcome": chk["outcome"],
            "observed_at": chk["observed_at"],
            "detail": chk.get("detail")
        })

    return {
        "count": len(logs),
        "logs": logs
    }


@app.get("/export.json")
def export_database_dump():
    """One-click complete database export for competition judges."""
    store = get_store()
    pages = store.list_pages()

    full_export = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "total_pages": len(pages),
        "pages": []
    }

    for p in pages:
        p_id = p["id"]
        versions = store.get_versions(p_id, limit=500)
        checks = store.get_checks(page_id=p_id, limit=100)
        full_export["pages"].append({
            "metadata": p,
            "version_count": len(versions),
            "versions": versions,
            "checks": checks
        })

    return full_export


@app.post("/internal/wake")
def wake_processor(secret: str | None = Query(None)):
    """Wakes the background event processor to immediately process pending events."""
    env_secret = os.getenv("INTERNAL_SECRET")
    if env_secret and secret != env_secret:
        return json_error("UNAUTHORIZED", "Invalid wake secret", status_code=403)

    count = processor_worker.wake()
    return {
        "status": "ok",
        "events_processed": count,
        "last_run_at": processor_worker.last_run_at
    }


@app.post("/webhook")
async def webhook_receiver(request: Request):
    """Direct receiver for TinyFish webhook deliveries."""
    store = get_store()
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    run_id = payload.get("id") or payload.get("run_id")
    monitor_id = payload.get("monitor_id")

    event_id = insert_incoming_event({
        "monitor_id": monitor_id,
        "run_id": run_id,
        "payload": payload
    }, store=store)

    # Wake background worker
    processor_worker.wake()

    return {
        "status": "received",
        "event_id": event_id
    }
