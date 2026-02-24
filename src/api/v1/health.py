"""Health check endpoints for HaqSetu API v1.

Provides liveness and readiness probes for Kubernetes / Cloud Run
deployments.  The readiness check verifies connectivity to critical
GCP services.
"""

from __future__ import annotations

import time

import structlog
from fastapi import APIRouter, Request
from pydantic import BaseModel

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

router = APIRouter(prefix="/health", tags=["health"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    """Basic health check response."""

    status: str
    version: str
    uptime_seconds: float


class ReadinessResponse(BaseModel):
    """Readiness check response with individual service statuses."""

    status: str
    checks: dict[str, str]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=HealthResponse)
async def health_check(request: Request) -> HealthResponse:
    """Liveness probe.

    Returns 200 if the application process is running and able to
    handle requests.  Does *not* check downstream dependencies.
    """
    start_time: float = getattr(request.app.state, "start_time", time.time())
    uptime = time.time() - start_time

    return HealthResponse(
        status="healthy",
        version=request.app.version,
        uptime_seconds=round(uptime, 2),
    )


@router.get("/ready", response_model=ReadinessResponse)
async def readiness_check(request: Request) -> ReadinessResponse:
    """Readiness probe.

    Verifies connectivity to critical GCP services (cache, translation)
    so the load balancer only routes traffic to fully-initialised
    instances.
    """
    checks: dict[str, str] = {}
    all_ok = True

    # -- Check cache connectivity ------------------------------------------
    cache = getattr(request.app.state, "cache", None)
    if cache is not None:
        try:
            await cache.set("_health_check", "ok", ttl_seconds=10)
            val = await cache.get("_health_check")
            if val == "ok":
                checks["cache"] = "ok"
            else:
                checks["cache"] = "degraded"
                all_ok = False
        except Exception as exc:
            checks["cache"] = f"error: {exc!s}"
            all_ok = False
    else:
        checks["cache"] = "not_configured"

    # -- Check translation service -----------------------------------------
    translation = getattr(request.app.state, "translation", None)
    if translation is not None:
        try:
            lang, conf = await translation.detect_language("hello")
            if lang and conf > 0:
                checks["translation"] = "ok"
            else:
                checks["translation"] = "degraded"
                all_ok = False
        except Exception as exc:
            checks["translation"] = f"error: {exc!s}"
            all_ok = False
    else:
        checks["translation"] = "not_configured"

    # -- Check scheme data loaded ------------------------------------------
    scheme_data = getattr(request.app.state, "scheme_data", None)
    if scheme_data is not None and len(scheme_data) > 0:
        checks["scheme_data"] = f"ok ({len(scheme_data)} schemes loaded)"
    else:
        checks["scheme_data"] = "no_data"
        all_ok = False

    # -- Check orchestrator ------------------------------------------------
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is not None:
        checks["orchestrator"] = "ok"
    else:
        checks["orchestrator"] = "not_initialised"
        all_ok = False

    status = "ready" if all_ok else "degraded"

    logger.info("health.readiness_check", status=status, checks=checks)

    return ReadinessResponse(status=status, checks=checks)
