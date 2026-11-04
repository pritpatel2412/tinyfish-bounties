"""
apps/timemachine/api.py
FastAPI REST API for Time Machine.
Provides complete history, as-of point-in-time reconstruction, structured diffs,
audit logs, webhook ingestion, and live export for judges.
"""

import os
import re
import json
import time
import uuid
import secrets
import logging
import urllib.parse
from collections import defaultdict
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
from tinycore.tf_client import TF
from apps.timemachine.capture import add_page
from apps.timemachine.processor import (
    processor_worker,
    insert_incoming_event
)
from apps.timemachine.watchdog import watchdog_worker

logger = logging.getLogger("timemachine.api")


@asynccontextmanager
async def tm_lifespan(app: FastAPI):
    # Production startup assertion: refuse to run with SQLite on ephemeral production hosts
    env = os.getenv("ENV", "").strip().lower()
    is_render = os.getenv("RENDER", "").strip().lower() in ("true", "1")
    backend = os.getenv("STORE_BACKEND", "sqlite").strip().lower()
    if (env == "production" or is_render) and backend != "supabase":
        raise RuntimeError(f"Production environment requires STORE_BACKEND=supabase (current: {backend})")

    # Startup: Launch background processor worker and hourly watchdog
    processor_worker.start()
    watchdog_worker.start()
    logger.info("Time Machine API initialized; processor and watchdog workers started.")
    yield
    # Shutdown
    processor_worker.stop()
    watchdog_worker.stop()
    logger.info("Time Machine API shutdown; workers stopped.")


app = create_app(
    title="Time Machine API",
    version="1.0.0",
    lifespan=tm_lifespan
)




# --- Security & Rate Limiting ---

_rate_limits = defaultdict(list)
RATE_LIMIT_PER_MINUTE = 60

_export_cache = {
    "cached_at": 0.0,
    "payloads": {}
}


def verify_admin_token(request: Request):
    """Enforces X-Admin-Token header for mutation endpoints using constant-time comparison."""
    admin_token = os.getenv("ADMIN_TOKEN", "").strip()
    is_prod = os.getenv("ENV") == "production" or os.getenv("RENDER") in ("true", "1")
    if not admin_token:
        if is_prod:
            raise HTTPException(status_code=401, detail="X-Admin-Token required (ADMIN_TOKEN not set in production)")
        return  # Allow in local test/dev when ADMIN_TOKEN is not configured
    token_hdr = request.headers.get("x-admin-token") or ""
    if not token_hdr or not secrets.compare_digest(token_hdr, admin_token):
        raise HTTPException(status_code=401, detail="Unauthorized: invalid or missing X-Admin-Token")


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """Modest rate limiter: 60 GET requests/min per client IP."""
    if request.method == "GET" and not request.url.path.startswith(("/test", "/health")):
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        minute_ago = now - 60.0
        recent = [t for t in _rate_limits[client_ip] if t > minute_ago]
        if len(recent) >= RATE_LIMIT_PER_MINUTE:
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "RATE_LIMITED",
                        "message": f"Rate limit exceeded: max {RATE_LIMIT_PER_MINUTE} requests per minute",
                        "path": str(request.url.path)
                    }
                }
            )
        recent.append(now)
        _rate_limits[client_ip] = recent

    response = await call_next(request)
    return response

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
def create_page_endpoint(req: PageCreateRequest, request: Request):
    verify_admin_token(request)
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
    active: bool | None = Query(None, description="Filter by active status"),
    include_test: bool = Query(False, description="Include test pages if true")
):
    """Lists all tracked pages with live monitoring status and version counts."""
    store = get_store()
    pages = store.list_pages()

    result = []
    for p in pages:
        if not include_test and p.get("is_test"):
            continue
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

@app.delete("/pages/{page_id}")
def delete_page_endpoint(page_id: str, request: Request):
    """Removes a tracked page and deletes its remote TinyFish monitor if active."""
    verify_admin_token(request)
    store = get_store()
    page = store.get_page(page_id)
    if not page:
        return json_error("PAGE_NOT_FOUND", f"Page with ID '{page_id}' not found", status_code=404)

    # If monitor is attached, remove monitor from TinyFish
    mon_id = page.get("monitor_id")
    if mon_id:
        try:
            api_key = os.getenv("TINYFISH_API_KEY")
            if api_key:
                tf = TF(api_key=api_key)
                tf.monitors_delete(mon_id)
        except Exception as e:
            logger.warning(f"Could not delete monitor {mon_id} on TinyFish: {e}")

    deleted = store.delete_page(page_id)
    return {"status": "deleted", "page_id": page_id, "deleted": deleted}


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
    min_materiality: float | None = Query(None, description="Minimum materiality score (0.0 - 1.0)"),
    include_test: bool = Query(False, description="Include test changes if true")
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

        if not include_test and page.get("is_test"):
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
def public_logs_feed(
    limit: int = Query(100, ge=1, le=500, description="Number of log rows to return"),
    include_test: bool = Query(False, description="Include test logs if true")
):
    """Public audit feed of monitoring checks proving continuous live operation."""
    store = get_store()
    all_pages = {p["id"]: p for p in store.list_pages()}
    checks = store.get_checks(limit=limit)

    logs = []
    for chk in checks:
        page = all_pages.get(chk["page_id"], {})
        if not include_test and page.get("is_test"):
            continue
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
def export_database_dump(
    include_test: bool = Query(False, description="Include test pages if true")
):
    """One-click complete database export for competition judges (cached 5 min)."""
    now = time.time()
    cache_key = "test" if include_test else "prod"
    if (now - _export_cache["cached_at"] < 300) and (cache_key in _export_cache["payloads"]):
        return _export_cache["payloads"][cache_key]

    store = get_store()
    pages = store.list_pages()
    if not include_test:
        pages = [p for p in pages if not p.get("is_test")]

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

    _export_cache["cached_at"] = now
    _export_cache["payloads"][cache_key] = full_export
    return full_export


@app.post("/internal/wake")
def wake_processor(request: Request, secret: str | None = Query(None)):
    """Wakes the background event processor to immediately process pending events."""
    wake_secret = (os.getenv("WAKE_SECRET") or os.getenv("INTERNAL_SECRET") or "").strip()
    if wake_secret:
        hdr_sec = request.headers.get("x-wake-secret") or request.headers.get("authorization", "").replace("Bearer ", "")
        provided = secret or hdr_sec or ""
        if not secrets.compare_digest(provided, wake_secret):
            return json_error("UNAUTHORIZED", "Invalid or missing wake secret", status_code=401)

    count = processor_worker.wake()
    return {
        "status": "ok",
        "events_processed": count,
        "last_run_at": processor_worker.last_run_at
    }


@app.post("/webhook")
async def webhook_receiver(request: Request):
    """Direct receiver for TinyFish webhook deliveries."""
    webhook_secret = os.getenv("WEBHOOK_SECRET", "").strip()
    if webhook_secret:
        query_sec = request.query_params.get("secret") or ""
        hdr_sec = request.headers.get("x-webhook-secret") or ""
        provided = query_sec or hdr_sec
        if not secrets.compare_digest(provided, webhook_secret):
            return json_error("UNAUTHORIZED", "Invalid or missing webhook secret", status_code=401)
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


# --- Mutable Test Page (for live change verification) ---

_test_page_state = {
    "title": "Acme Cloud Platform Pricing",
    "plan_name": "Standard Plan",
    "price": "$29/month",
    "features": [
        "Up to 5 team members",
        "10GB cloud storage",
        "Standard email support"
    ],
    "last_updated": "2026-11-01T00:00:00Z"
}


@app.get("/testpage")
def get_testpage():
    """Serves an HTML page representing a live pricing table that can be dynamically edited."""
    from fastapi.responses import HTMLResponse
    features_html = "\n".join(f"      <li>{f}</li>" for f in _test_page_state["features"])
    html_content = f"""<!DOCTYPE html>
<html>
<head>
  <title>{_test_page_state["title"]}</title>
  <style>
    body {{ font-family: sans-serif; padding: 2rem; max-width: 600px; margin: 0 auto; }}
    .card {{ border: 1px solid #ddd; border-radius: 8px; padding: 1.5rem; }}
    .price {{ font-size: 2rem; font-weight: bold; color: #2563eb; margin: 1rem 0; }}
  </style>
</head>
<body>
  <h1>{_test_page_state["title"]}</h1>
  <div class="card">
    <h2>{_test_page_state["plan_name"]}</h2>
    <div class="price">{_test_page_state["price"]}</div>
    <ul>
{features_html}
    </ul>
  </div>
</body>
</html>"""
    return HTMLResponse(content=html_content)


class TestPageEditRequest(BaseModel):
    title: str | None = None
    plan_name: str | None = None
    price: str | None = None
    features: list[str] | None = None


@app.post("/testpage/edit")
def edit_testpage(req: TestPageEditRequest, request: Request):
    verify_admin_token(request)
    """Mutates the test page to trigger a real detectable content change."""
    global _test_page_state
    if req.title:
        _test_page_state["title"] = req.title
    if req.plan_name:
        _test_page_state["plan_name"] = req.plan_name
    if req.price:
        _test_page_state["price"] = req.price
    if req.features is not None:
        _test_page_state["features"] = req.features
    _test_page_state["last_updated"] = datetime.now(timezone.utc).isoformat()
    return {"status": "updated", "state": _test_page_state}


class PageSuggestRequest(BaseModel):
    url: str
    title: str | None = None
    reason: str | None = None


@app.post("/suggest", status_code=status.HTTP_202_ACCEPTED)
def suggest_page_endpoint(req: PageSuggestRequest):
    """Queues a user-suggested page for administrator review (no direct monitor creation)."""
    parsed = urllib.parse.urlparse(req.url)
    if not parsed.scheme or not parsed.netloc:
        return json_error("INVALID_URL", "Valid http/https URL required", status_code=400)

    suggestion = {
        "id": str(uuid.uuid4()),
        "url": req.url,
        "title": req.title or parsed.netloc,
        "reason": req.reason or "Community suggestion",
        "suggested_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending_approval"
    }
    os.makedirs("data", exist_ok=True)
    with open(os.path.join("data", "suggestions.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(suggestion) + "\n")

    return {
        "status": "queued",
        "message": "Suggestion queued for admin approval",
        "suggestion": suggestion
    }
