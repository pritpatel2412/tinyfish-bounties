import secrets
import time

"""
tinycore/api_common.py
Standardized FastAPI application factory providing:
- Standard CORS middleware
- Consistent JSON error envelopes: {"error": {"code": "...", "message": "...", "source_url": "..."}}
- /health: Public health check (status, uptime, processor heartbeat, version)
- /admin/health: Protected telemetry (wallet balance, daily spend, monitors, ledger) requiring X-Admin-Token
- /endpoint-usage: Aggregates data/tf_calls.jsonl by endpoint with call counts, latency, and estimated cost
"""

import json
import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from tinycore.ledger import CostLedger, MonitorBudget

CALLS_LOG = os.path.join("data", "tf_calls.jsonl")


def get_endpoint_usage_summary() -> dict:
    """Aggregates calls logged in data/tf_calls.jsonl by endpoint."""
    if not os.path.exists(CALLS_LOG):
        return {
            "total_calls": 0,
            "total_cost_usd": 0.0,
            "endpoints": {}
        }

    endpoints: dict[str, dict] = {}
    total_calls = 0
    total_cost = 0.0

    with open(CALLS_LOG, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except Exception:
                continue

            ep = entry.get("endpoint", "unknown")
            ok = bool(entry.get("ok", True))
            lat = float(entry.get("latency_ms", 0.0))
            cost = float(entry.get("cost_usd", 0.0))

            if ep not in endpoints:
                endpoints[ep] = {
                    "count": 0,
                    "success": 0,
                    "failed": 0,
                    "total_latency_ms": 0.0,
                    "total_cost_usd": 0.0
                }

            endpoints[ep]["count"] += 1
            if ok:
                endpoints[ep]["success"] += 1
            else:
                endpoints[ep]["failed"] += 1
            endpoints[ep]["total_latency_ms"] += lat
            endpoints[ep]["total_cost_usd"] += cost

            total_calls += 1
            total_cost += cost

    summary = {}
    for ep, data in endpoints.items():
        cnt = data["count"]
        summary[ep] = {
            "total_calls": cnt,
            "success": data["success"],
            "failed": data["failed"],
            "avg_latency_ms": round(data["total_latency_ms"] / cnt, 2) if cnt else 0.0,
            "total_cost_usd": round(data["total_cost_usd"], 6)
        }

    return {
        "total_calls": total_calls,
        "total_cost_usd": round(total_cost, 6),
        "endpoints": summary
    }


_app_boot_epoch = time.time()

def create_app(title: str = "TinyFish Service", version: str = "1.0.0", lifespan=None, health_provider=None) -> FastAPI:
    """Creates a configured FastAPI application with standard monitoring routes and error envelopes."""
    app = FastAPI(title=title, version=version, lifespan=lifespan)

    # Route-aware CORS middleware:
    # Allows GET from any origin (including Base44 web apps).
    # Allows POST + OPTIONS on /suggest only; all other endpoints remain GET-only for CORS.
    @app.middleware("http")
    async def route_cors_middleware(request: Request, call_next):
        origin = request.headers.get("origin")
        path = request.url.path
        is_suggest = (path == "/suggest")

        # Handle Preflight OPTIONS
        if request.method == "OPTIONS":
            resp = Response(status_code=204)
            if origin:
                resp.headers["Access-Control-Allow-Origin"] = origin
                resp.headers["Access-Control-Allow-Credentials"] = "false"
                resp.headers["Access-Control-Allow-Headers"] = request.headers.get("access-control-request-headers", "*")
                if is_suggest:
                    resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
                else:
                    resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
                resp.headers["Access-Control-Max-Age"] = "86400"
            return resp

        response = await call_next(request)
        if origin:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "false"
            if is_suggest:
                response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
            else:
                response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        return response

    # Global error handler envelope
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        status_code = 500
        code = "INTERNAL_SERVER_ERROR"
        if hasattr(exc, "status_code"):
            status_code = exc.status_code
            code = exc.__class__.__name__

        return JSONResponse(
            status_code=status_code,
            content={
                "error": {
                    "code": code,
                    "message": str(exc),
                    "path": str(request.url.path)
                }
            }
        )

    @app.get("/health")
    def health_check():
        """Public health check: returns status, uptime, processor heartbeat age, and version."""
        now_epoch = time.time()
        now_utc = datetime.now(timezone.utc).isoformat()
        uptime_s = round(now_epoch - _app_boot_epoch, 2)

        extra = {}
        if health_provider:
            try:
                extra = health_provider()
            except Exception:
                pass

        return {
            "status": extra.get("status", "healthy"),
            "uptime_s": uptime_s,
            "processor_heartbeat_age_s": extra.get("processor_heartbeat_age_s", 0.0),
            "version": version,
            "timestamp": now_utc
        }

    @app.get("/admin/health")
    def admin_health_check(request: Request):
        """Admin-protected health & telemetry: requires X-Admin-Token."""
        admin_token = os.getenv("ADMIN_TOKEN", "").strip()
        is_prod = os.getenv("ENV") == "production" or os.getenv("RENDER") in ("true", "1")
        token_hdr = request.headers.get("x-admin-token") or ""
        if admin_token:
            if not token_hdr or not secrets.compare_digest(token_hdr, admin_token):
                raise HTTPException(status_code=401, detail="Unauthorized: invalid or missing X-Admin-Token")
        elif is_prod:
            raise HTTPException(status_code=401, detail="X-Admin-Token required (ADMIN_TOKEN not set in production)")

        now_epoch = time.time()
        now_utc = datetime.now(timezone.utc).isoformat()
        daily_spend = CostLedger.get_daily_spend()
        max_daily = CostLedger.get_max_daily()
        budget_status = MonitorBudget.get_status()
        uptime_s = round(now_epoch - _app_boot_epoch, 2)

        extra = {}
        if health_provider:
            try:
                extra = health_provider()
            except Exception:
                pass

        # Git sha resolution
        git_sha = extra.get("git_sha")
        if not git_sha:
            git_sha = os.getenv("GIT_SHA") or os.getenv("RENDER_GIT_COMMIT")
            if not git_sha:
                try:
                    import subprocess
                    res = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=2)
                    if res.returncode == 0:
                        git_sha = res.stdout.strip()
                except Exception:
                    pass
            git_sha = (git_sha or "unknown")[:8]

        return {
            "status": extra.get("status", "healthy"),
            "version": version,
            "timestamp": now_utc,
            "uptime_s": uptime_s,
            "processor_heartbeat_age_s": extra.get("processor_heartbeat_age_s", 0.0),
            "unprocessed_events_count": extra.get("unprocessed_events_count", 0),
            "monitors_active": extra.get("monitors_active", budget_status.get("total_active", 0)),
            "monitors_paused": extra.get("monitors_paused", 0),
            "wallet_balance_usd": extra.get("wallet_balance_usd", None),
            "daily_spend_usd": round(daily_spend, 4),
            "store_backend": os.getenv("STORE_BACKEND", "sqlite").strip().lower(),
            "git_sha": git_sha,
            "ledger": {
                "today_spend_usd": round(daily_spend, 4),
                "max_daily_usd": max_daily,
                "headroom_usd": round(max(0.0, max_daily - daily_spend), 4)
            },
            "monitors": budget_status,
            "degraded_reasons": extra.get("degraded_reasons", [])
        }

    @app.get("/endpoint-usage")
    def endpoint_usage():
        return get_endpoint_usage_summary()

    return app
