"""
tinycore/api_common.py
Standardized FastAPI application factory providing:
- Standard CORS middleware
- Consistent JSON error envelopes: {"error": {"code": "...", "message": "...", "source_url": "..."}}
- /health: Poller heartbeat, ledger daily spend, monitor allocations & budget
- /endpoint-usage: Aggregates data/tf_calls.jsonl by endpoint with call counts, latency, and estimated cost
"""

import os
import json
from datetime import datetime, timezone
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

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


def create_app(title: str = "TinyFish Service", version: str = "1.0.0", lifespan=None) -> FastAPI:
    """Creates a configured FastAPI application with standard monitoring routes and error envelopes."""
    app = FastAPI(title=title, version=version, lifespan=lifespan)

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

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
        now_utc = datetime.now(timezone.utc).isoformat()
        daily_spend = CostLedger.get_daily_spend()
        max_daily = CostLedger.get_max_daily()
        budget_status = MonitorBudget.get_status()

        return {
            "status": "healthy",
            "timestamp": now_utc,
            "ledger": {
                "today_spend_usd": round(daily_spend, 4),
                "max_daily_usd": max_daily,
                "headroom_usd": round(max(0.0, max_daily - daily_spend), 4)
            },
            "monitors": budget_status
        }

    @app.get("/endpoint-usage")
    def endpoint_usage():
        return get_endpoint_usage_summary()

    return app
