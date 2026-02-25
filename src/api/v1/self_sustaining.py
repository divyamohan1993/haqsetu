"""Self-Sustaining Infrastructure API endpoints for HaqSetu.

Provides endpoints for monitoring the health, sustainability, and
automated operations of the platform.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request

from src.middleware.auth import require_admin_api_key

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/sustainability", tags=["self-sustaining"])


@router.get("/dashboard")
async def get_sustainability_dashboard(request: Request) -> dict:
    """Get the self-sustaining operations dashboard.

    Shows health status, cost tracking, stale data alerts,
    and upcoming scheduled tasks.
    """
    service = getattr(request.app.state, "self_sustaining", None)
    if service is None:
        return {
            "status": "basic",
            "message": "Self-sustaining service not initialized. Core services operational.",
            "health": {"api": "healthy", "database": "unknown", "cache": "unknown"},
        }

    try:
        dashboard = await service.get_sustainability_dashboard()
    except Exception:
        logger.error("api.sustainability.dashboard_failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to load dashboard") from None

    return {
        "overall_health": dashboard.overall_health,
        "services": dashboard.service_statuses,
        "cost_report": {
            "current_month_cost": dashboard.cost_report.current_month_cost,
            "budget_limit": dashboard.cost_report.budget_limit,
            "utilization_pct": dashboard.cost_report.utilization_pct,
            "alerts": dashboard.cost_report.alerts,
        },
        "stale_data_alerts": [
            {
                "scheme_id": alert.scheme_id,
                "scheme_name": alert.scheme_name,
                "days_since_verified": alert.days_since_verified,
                "priority": alert.priority,
            }
            for alert in dashboard.stale_data_alerts
        ],
        "scheduled_tasks": [
            {
                "task_name": task.task_name,
                "schedule": task.schedule,
                "last_run": task.last_run.isoformat() if task.last_run else None,
                "next_run": task.next_run.isoformat() if task.next_run else None,
                "status": task.status,
            }
            for task in dashboard.scheduled_tasks
        ],
        "last_updated": dashboard.last_updated.isoformat(),
    }


@router.post("/health-check", dependencies=[Depends(require_admin_api_key)])
async def run_health_check(request: Request) -> dict:
    """Run a comprehensive health check on all services.

    Checks: API server, Firestore, Redis, Vertex AI, Translation,
    Speech services, and external verification sources.
    """
    service = getattr(request.app.state, "self_sustaining", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Self-sustaining service not available")

    try:
        report = await service.run_health_check()
    except Exception:
        logger.error("api.sustainability.health_check_failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Health check failed") from None

    return {
        "overall_status": report.overall_status,
        "checks": report.checks,
        "degraded_services": report.degraded_services,
        "recommendations": report.recommendations,
        "checked_at": report.checked_at.isoformat(),
    }


@router.post("/auto-update", dependencies=[Depends(require_admin_api_key)])
async def trigger_auto_update(request: Request) -> dict:
    """Trigger automated scheme data update.

    Polls for new gazette notifications, scheme updates,
    and re-verifies stale data.
    """
    service = getattr(request.app.state, "self_sustaining", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Self-sustaining service not available")

    try:
        report = await service.auto_update_schemes()
    except Exception:
        logger.error("api.sustainability.auto_update_failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Auto-update failed") from None

    return {
        "schemes_updated": report.schemes_updated,
        "schemes_added": report.schemes_added,
        "schemes_reverified": report.schemes_reverified,
        "errors": report.errors,
        "duration_seconds": report.duration_seconds,
    }


@router.get("/stale-data", dependencies=[Depends(require_admin_api_key)])
async def get_stale_data_alerts(request: Request) -> dict:
    """Get alerts for stale scheme data needing re-verification."""
    service = getattr(request.app.state, "self_sustaining", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Self-sustaining service not available")

    try:
        alerts = await service.detect_stale_data()
    except Exception:
        logger.error("api.sustainability.stale_data_failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to detect stale data") from None

    return {
        "total_stale": len(alerts),
        "alerts": [
            {
                "scheme_id": a.scheme_id,
                "scheme_name": a.scheme_name,
                "days_since_verified": a.days_since_verified,
                "priority": a.priority,
                "recommended_action": a.recommended_action,
            }
            for a in alerts
        ],
    }


@router.get("/cost-report", dependencies=[Depends(require_admin_api_key)])
async def get_cost_report(request: Request) -> dict:
    """Get GCP cost tracking and budget utilization report."""
    service = getattr(request.app.state, "self_sustaining", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Self-sustaining service not available")

    try:
        report = await service.check_cost_budget()
    except Exception:
        logger.error("api.sustainability.cost_report_failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get cost report") from None

    return {
        "current_month_cost": report.current_month_cost,
        "budget_limit": report.budget_limit,
        "utilization_pct": report.utilization_pct,
        "cost_by_service": report.cost_by_service,
        "alerts": report.alerts,
        "recommendations": report.recommendations,
    }
