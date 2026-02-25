"""Super Admin Disaster Recovery & System Management API for HaqSetu.

Provides endpoints for:
    * System state snapshots (export/import)
    * Emergency maintenance mode toggle
    * Data backup & rollback triggers
    * Cache flush and rebuild
    * Audit log of admin actions
    * System health overview with auto-fix recommendations

All endpoints require admin API key authentication.
"""

from __future__ import annotations

import copy
import hashlib
import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from src.middleware.auth import require_admin_api_key

logger = structlog.get_logger(__name__)

router = APIRouter(
    prefix="/admin/recovery",
    tags=["admin-recovery"],
    dependencies=[Depends(require_admin_api_key)],
)


# ---------------------------------------------------------------------------
# In-memory state for disaster recovery
# ---------------------------------------------------------------------------

_snapshots: dict[str, dict[str, Any]] = {}
_admin_audit_log: list[dict[str, Any]] = []
_maintenance_mode: dict[str, Any] = {
    "enabled": False,
    "message": "",
    "enabled_at": None,
    "enabled_by": "system",
}
_rollback_points: list[dict[str, Any]] = []


def _record_audit(action: str, details: str, admin_ip: str = "unknown") -> None:
    """Record an admin action in the audit log."""
    entry = {
        "id": uuid4().hex[:12],
        "action": action,
        "details": details,
        "admin_ip": admin_ip,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    _admin_audit_log.insert(0, entry)
    # Keep only last 1000 entries
    if len(_admin_audit_log) > 1000:
        _admin_audit_log[:] = _admin_audit_log[:1000]
    logger.info("admin.audit", **entry)


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class MaintenanceModeRequest(BaseModel):
    enabled: bool = Field(..., description="Enable or disable maintenance mode")
    message: str = Field(
        default="System is undergoing scheduled maintenance. Please try again shortly.",
        max_length=500,
        description="Message shown to users during maintenance",
    )


class SnapshotResponse(BaseModel):
    snapshot_id: str
    created_at: str
    components: list[str]
    size_estimate: str
    checksum: str


class RollbackRequest(BaseModel):
    snapshot_id: str = Field(..., description="ID of the snapshot to rollback to")
    components: list[str] = Field(
        default_factory=lambda: ["all"],
        description="Components to rollback: schemes, feedback, profiles, cache, all",
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/status")
async def get_system_status(request: Request) -> dict[str, Any]:
    """Get comprehensive system status for disaster recovery dashboard.

    Returns health of all subsystems, data integrity checks,
    backup status, and recommended actions.
    """
    admin_ip = request.client.host if request.client else "unknown"
    _record_audit("status_check", "System status requested", admin_ip)

    # Collect subsystem statuses
    cache = getattr(request.app.state, "cache", None)
    scheme_data = getattr(request.app.state, "scheme_data", [])
    verification_results = getattr(request.app.state, "verification_results", {})
    self_sustaining = getattr(request.app.state, "self_sustaining", None)

    # Import in-memory stores to check their state
    from src.api.v1.feedback import _feedback_store
    from src.api.v1.profile import _profiles

    subsystems = {
        "api_server": "healthy",
        "scheme_data": "healthy" if scheme_data else "degraded",
        "verification_engine": "healthy" if verification_results else "no_data",
        "cache": "unknown",
        "profiles_store": f"{len(_profiles)} profiles loaded",
        "feedback_store": f"{len(_feedback_store)} entries",
        "maintenance_mode": _maintenance_mode["enabled"],
        "self_sustaining": "active" if self_sustaining else "inactive",
    }

    # Check cache health
    if cache is not None:
        try:
            cache_backend = await cache._backend()
            if hasattr(cache_backend, "ping"):
                subsystems["cache"] = "healthy" if await cache_backend.ping() else "degraded"
            else:
                subsystems["cache"] = "inmemory_fallback"
        except Exception:
            subsystems["cache"] = "error"

    # Data integrity
    integrity = {
        "schemes_loaded": len(scheme_data),
        "schemes_verified": len(verification_results),
        "verification_coverage_pct": (
            round(len(verification_results) / len(scheme_data) * 100, 1)
            if scheme_data
            else 0
        ),
        "snapshots_available": len(_snapshots),
        "rollback_points": len(_rollback_points),
    }

    # Recommendations
    recommendations = []
    if not scheme_data:
        recommendations.append(
            "CRITICAL: No scheme data loaded. Run /admin/ingest to load data."
        )
    if not _snapshots:
        recommendations.append(
            "WARNING: No snapshots exist. Create a snapshot for disaster recovery."
        )
    if len(_profiles) > 0 and not _snapshots:
        recommendations.append(
            "WARNING: User profiles exist but no backup. Create a snapshot now."
        )
    if _maintenance_mode["enabled"]:
        recommendations.append(
            "INFO: Maintenance mode is active. Users see maintenance message."
        )
    if not recommendations:
        recommendations.append("All systems operational. No action required.")

    return {
        "status": "maintenance" if _maintenance_mode["enabled"] else "operational",
        "subsystems": subsystems,
        "data_integrity": integrity,
        "maintenance_mode": _maintenance_mode,
        "recommendations": recommendations,
        "last_checked": datetime.now(UTC).isoformat(),
    }


@router.post("/maintenance")
async def toggle_maintenance_mode(
    body: MaintenanceModeRequest,
    request: Request,
) -> dict[str, Any]:
    """Enable or disable maintenance mode.

    When enabled, the middleware returns 503 for all non-admin,
    non-health endpoints with the configured message.
    """
    admin_ip = request.client.host if request.client else "unknown"

    _maintenance_mode["enabled"] = body.enabled
    _maintenance_mode["message"] = body.message
    _maintenance_mode["enabled_at"] = (
        datetime.now(UTC).isoformat() if body.enabled else None
    )
    _maintenance_mode["enabled_by"] = admin_ip

    # Store in app state so middleware can access it
    request.app.state.maintenance_mode = _maintenance_mode

    action = "enabled" if body.enabled else "disabled"
    _record_audit(
        f"maintenance_{action}",
        f"Maintenance mode {action}: {body.message[:100]}",
        admin_ip,
    )

    return {
        "maintenance_mode": _maintenance_mode,
        "message": f"Maintenance mode {action} successfully.",
    }


@router.post("/snapshot", response_model=SnapshotResponse)
async def create_snapshot(request: Request) -> SnapshotResponse:
    """Create a point-in-time snapshot of all system data.

    Captures: scheme data, verification results, feedback,
    user profiles, and system configuration. Snapshots can be
    used for rollback in case of data corruption.
    """
    admin_ip = request.client.host if request.client else "unknown"

    scheme_data = getattr(request.app.state, "scheme_data", [])
    verification_results = getattr(request.app.state, "verification_results", {})

    from src.api.v1.feedback import _feedback_index, _feedback_store
    from src.api.v1.profile import _profiles

    snapshot_id = f"snap-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"

    # Deep copy data to prevent mutation
    snapshot_data: dict[str, Any] = {
        "snapshot_id": snapshot_id,
        "created_at": datetime.now(UTC).isoformat(),
        "components": [],
        "data": {},
    }

    # Capture schemes
    if scheme_data:
        snapshot_data["data"]["schemes"] = [
            s.model_dump(mode="json") if hasattr(s, "model_dump") else str(s)
            for s in scheme_data
        ]
        snapshot_data["components"].append("schemes")

    # Capture verification results
    if verification_results:
        snapshot_data["data"]["verification"] = copy.deepcopy(verification_results)
        snapshot_data["components"].append("verification")

    # Capture feedback
    if _feedback_store:
        snapshot_data["data"]["feedback"] = {
            fid: fb.model_dump(mode="json")
            for fid, fb in _feedback_store.items()
        }
        snapshot_data["data"]["feedback_index"] = list(_feedback_index)
        snapshot_data["components"].append("feedback")

    # Capture profiles
    if _profiles:
        snapshot_data["data"]["profiles"] = {
            pid: p.model_dump(mode="json") if hasattr(p, "model_dump") else str(p)
            for pid, p in _profiles.items()
        }
        snapshot_data["components"].append("profiles")

    # Calculate checksum for integrity verification
    import json

    raw = json.dumps(snapshot_data["data"], sort_keys=True, default=str)
    checksum = hashlib.sha256(raw.encode()).hexdigest()[:16]
    snapshot_data["checksum"] = checksum

    # Store snapshot
    _snapshots[snapshot_id] = snapshot_data

    # Also create a rollback point
    _rollback_points.insert(0, {
        "snapshot_id": snapshot_id,
        "created_at": snapshot_data["created_at"],
        "components": snapshot_data["components"],
        "checksum": checksum,
    })
    # Keep max 50 rollback points
    if len(_rollback_points) > 50:
        oldest_id = _rollback_points.pop()["snapshot_id"]
        _snapshots.pop(oldest_id, None)

    size_kb = len(raw) / 1024
    size_str = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb / 1024:.1f} MB"

    _record_audit(
        "snapshot_created",
        f"Snapshot {snapshot_id} created ({size_str}, {len(snapshot_data['components'])} components)",
        admin_ip,
    )

    return SnapshotResponse(
        snapshot_id=snapshot_id,
        created_at=snapshot_data["created_at"],
        components=snapshot_data["components"],
        size_estimate=size_str,
        checksum=checksum,
    )


@router.get("/snapshots")
async def list_snapshots() -> dict[str, Any]:
    """List all available snapshots for rollback."""
    return {
        "snapshots": _rollback_points,
        "total": len(_rollback_points),
    }


@router.post("/rollback")
async def rollback_to_snapshot(
    body: RollbackRequest,
    request: Request,
) -> dict[str, Any]:
    """Rollback system state to a previous snapshot.

    Restores specified components (schemes, feedback, profiles,
    verification) to the state captured in the snapshot.
    """
    admin_ip = request.client.host if request.client else "unknown"

    snapshot = _snapshots.get(body.snapshot_id)
    if snapshot is None:
        raise HTTPException(
            status_code=404,
            detail=f"Snapshot '{body.snapshot_id}' not found.",
        )

    data = snapshot.get("data", {})
    components_to_restore = body.components
    if "all" in components_to_restore:
        components_to_restore = ["schemes", "verification", "feedback", "profiles"]

    restored = []
    errors = []

    for component in components_to_restore:
        try:
            if component == "schemes" and "schemes" in data:
                from src.models.scheme import SchemeDocument

                schemes = []
                for s_data in data["schemes"]:
                    if isinstance(s_data, dict):
                        schemes.append(SchemeDocument(**s_data))
                request.app.state.scheme_data = schemes
                restored.append(f"schemes ({len(schemes)} records)")

            elif component == "verification" and "verification" in data:
                request.app.state.verification_results = copy.deepcopy(
                    data["verification"]
                )
                restored.append(
                    f"verification ({len(data['verification'])} records)"
                )

            elif component == "feedback" and "feedback" in data:
                from src.api.v1.feedback import _feedback_index, _feedback_store
                from src.models.feedback import CitizenFeedback

                _feedback_store.clear()
                _feedback_index.clear()
                for fid, fb_data in data["feedback"].items():
                    _feedback_store[fid] = CitizenFeedback(**fb_data)
                _feedback_index.extend(data.get("feedback_index", []))
                restored.append(
                    f"feedback ({len(data['feedback'])} records)"
                )

            elif component == "profiles" and "profiles" in data:
                from src.api.v1.profile import _profiles
                from src.models.user_profile import UserProfile

                _profiles.clear()
                for pid, p_data in data["profiles"].items():
                    if isinstance(p_data, dict):
                        _profiles[pid] = UserProfile(**p_data)
                restored.append(
                    f"profiles ({len(data['profiles'])} records)"
                )

        except Exception as exc:
            errors.append(f"{component}: restore failed")
            logger.error(
                "admin.rollback.component_failed",
                component=component,
                error=str(exc),
                exc_info=True,
            )

    _record_audit(
        "rollback_executed",
        f"Rollback to {body.snapshot_id}: restored={restored}, errors={errors}",
        admin_ip,
    )

    return {
        "snapshot_id": body.snapshot_id,
        "restored_components": restored,
        "errors": errors,
        "success": len(errors) == 0,
        "message": (
            f"Successfully restored {len(restored)} component(s)."
            if not errors
            else f"Restored {len(restored)} component(s) with {len(errors)} error(s)."
        ),
    }


@router.post("/cache/flush")
async def flush_cache(request: Request) -> dict[str, Any]:
    """Flush all cached data and rebuild from source.

    Use when cache data becomes stale or corrupted.
    """
    admin_ip = request.client.host if request.client else "unknown"
    cache = getattr(request.app.state, "cache", None)

    if cache is None:
        return {"message": "No cache backend configured.", "flushed": False}

    try:
        # Close and reinitialise the cache
        await cache.close()

        from config.settings import settings

        from src.services.cache import CacheManager

        new_cache = CacheManager(
            redis_url=settings.redis_url,
            namespace="haqsetu:",
        )
        request.app.state.cache = new_cache

        _record_audit("cache_flushed", "Cache flushed and rebuilt", admin_ip)

        return {
            "message": "Cache flushed and rebuilt successfully.",
            "flushed": True,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    except Exception as exc:
        logger.error("admin.cache_flush_failed", error=str(exc), exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Cache flush failed: {exc!s}",
        )


@router.post("/data/validate")
async def validate_data_integrity(request: Request) -> dict[str, Any]:
    """Run data integrity checks across all stores.

    Validates scheme data consistency, feedback references,
    and profile completeness.
    """
    admin_ip = request.client.host if request.client else "unknown"

    scheme_data = getattr(request.app.state, "scheme_data", [])
    verification_results = getattr(request.app.state, "verification_results", {})

    from src.api.v1.feedback import _feedback_store
    from src.api.v1.profile import _profiles

    issues: list[str] = []
    warnings: list[str] = []

    # Check scheme data
    scheme_ids = set()
    for s in scheme_data:
        sid = getattr(s, "scheme_id", None) or ""
        if not sid:
            issues.append("Found scheme with empty scheme_id")
        elif sid in scheme_ids:
            issues.append(f"Duplicate scheme_id: {sid}")
        scheme_ids.add(sid)

    # Check verification references
    orphan_verifications = [
        vid for vid in verification_results if vid not in scheme_ids
    ]
    if orphan_verifications:
        warnings.append(
            f"{len(orphan_verifications)} verification results reference non-existent schemes"
        )

    # Check feedback references
    orphan_feedback = 0
    for fb in _feedback_store.values():
        if fb.scheme_id and fb.scheme_id not in scheme_ids:
            orphan_feedback += 1
    if orphan_feedback:
        warnings.append(
            f"{orphan_feedback} feedback entries reference non-existent schemes"
        )

    # Check profiles
    incomplete_profiles = 0
    for p in _profiles.values():
        if not p.consent_given:
            issues.append(f"Profile {p.profile_id} missing consent")
        if not p.state and not p.pin_code:
            incomplete_profiles += 1
    if incomplete_profiles:
        warnings.append(f"{incomplete_profiles} profiles have no location data")

    status = "healthy"
    if issues:
        status = "corrupted"
    elif warnings:
        status = "warnings"

    _record_audit(
        "data_validation",
        f"Integrity check: {status}, {len(issues)} issues, {len(warnings)} warnings",
        admin_ip,
    )

    return {
        "status": status,
        "issues": issues,
        "warnings": warnings,
        "summary": {
            "schemes": len(scheme_data),
            "verifications": len(verification_results),
            "feedback": len(_feedback_store),
            "profiles": len(_profiles),
        },
        "checked_at": datetime.now(UTC).isoformat(),
    }


@router.get("/audit-log")
async def get_audit_log(
    limit: int = 50,
) -> dict[str, Any]:
    """Get the admin action audit log.

    Returns the most recent admin actions for accountability
    and forensic analysis.
    """
    return {
        "entries": _admin_audit_log[:limit],
        "total": len(_admin_audit_log),
    }


@router.post("/auto-fix")
async def auto_fix_issues(request: Request) -> dict[str, Any]:
    """Automatically fix detected data integrity issues.

    Runs validation, then attempts to resolve issues:
    - Removes orphaned verification results
    - Cleans up feedback referencing deleted schemes
    - Rebuilds indexes
    """
    admin_ip = request.client.host if request.client else "unknown"

    scheme_data = getattr(request.app.state, "scheme_data", [])
    verification_results = getattr(request.app.state, "verification_results", {})
    scheme_ids = {
        getattr(s, "scheme_id", "") for s in scheme_data
    }

    fixes_applied: list[str] = []

    # Fix 1: Remove orphaned verification results
    orphan_keys = [k for k in verification_results if k not in scheme_ids]
    for k in orphan_keys:
        del verification_results[k]
    if orphan_keys:
        fixes_applied.append(
            f"Removed {len(orphan_keys)} orphaned verification results"
        )

    # Fix 2: Rebuild feedback index
    from src.api.v1.feedback import _feedback_index, _feedback_store

    old_len = len(_feedback_index)
    # Remove index entries that don't exist in store
    valid_ids = [fid for fid in _feedback_index if fid in _feedback_store]
    _feedback_index.clear()
    _feedback_index.extend(valid_ids)
    if old_len != len(_feedback_index):
        fixes_applied.append(
            f"Cleaned feedback index: {old_len} -> {len(_feedback_index)} entries"
        )

    # Fix 3: Ensure all feedback store entries are in the index
    missing_from_index = [
        fid for fid in _feedback_store if fid not in _feedback_index
    ]
    for fid in missing_from_index:
        _feedback_index.append(fid)
    if missing_from_index:
        fixes_applied.append(
            f"Added {len(missing_from_index)} missing entries to feedback index"
        )

    if not fixes_applied:
        fixes_applied.append("No issues found. System is clean.")

    _record_audit(
        "auto_fix",
        f"Auto-fix applied: {'; '.join(fixes_applied)}",
        admin_ip,
    )

    return {
        "fixes_applied": fixes_applied,
        "timestamp": datetime.now(UTC).isoformat(),
    }


# ---------------------------------------------------------------------------
# AI-Powered Auto-Fix Orchestrator Endpoints (Gemini 3 Pro / Claude CLI)
# ---------------------------------------------------------------------------


class ClaudeRunRequest(BaseModel):
    """Request body for running Claude CLI auto-fix."""

    prompt: str | None = Field(
        default=None,
        max_length=2000,
        description="Custom prompt for Claude CLI. Uses default auto-fix prompt if omitted.",
    )
    timeout: int = Field(
        default=600,
        ge=30,
        le=1800,
        description="Maximum execution time in seconds (30-1800)",
    )


class OrchestratorRunRequest(BaseModel):
    """Request body for running the AI auto-fix orchestrator."""

    execute_fixes: bool = Field(
        default=True,
        description="Whether to execute fixes or just diagnose",
    )
    auto_approve: bool = Field(
        default=False,
        description="Auto-approve even critical fixes (use with caution)",
    )


@router.post("/orchestrator/run")
async def run_autofix_orchestrator(
    body: OrchestratorRunRequest,
    request: Request,
) -> dict[str, Any]:
    """Run the AI-powered auto-fix orchestrator.

    Uses Gemini 3 Pro (via Vertex AI) to:
    1. Analyze complete system state
    2. Diagnose root causes of any issues
    3. Generate and execute a safe fix plan
    4. Report results

    Falls back to rule-based diagnosis if Gemini is unavailable.
    """
    admin_ip = request.client.host if request.client else "unknown"

    # Get or create orchestrator
    orchestrator = getattr(request.app.state, "autofix_orchestrator", None)
    if orchestrator is None:
        from config.settings import settings
        from src.services.autofix_orchestrator import AutoFixOrchestrator

        orchestrator = AutoFixOrchestrator(
            project_id=settings.gcp_project_id,
            model_name=settings.vertex_ai_model.replace("flash", "pro")
            if "flash" in settings.vertex_ai_model
            else "gemini-3.0-pro",
            region=settings.vertex_ai_location,
        )
        request.app.state.autofix_orchestrator = orchestrator

    _record_audit(
        "orchestrator_run",
        f"AI auto-fix started (execute={body.execute_fixes}, auto_approve={body.auto_approve})",
        admin_ip,
    )

    report = await orchestrator.diagnose_and_fix(
        app_state=request.app.state,
        execute_fixes=body.execute_fixes,
        auto_approve=body.auto_approve,
    )

    _record_audit(
        "orchestrator_complete",
        f"AI auto-fix: {report.fixes_succeeded}/{report.fixes_executed} succeeded, "
        f"source={report.diagnosis_source}",
        admin_ip,
    )

    return {
        "report_id": report.report_id,
        "diagnosis_source": report.diagnosis_source,
        "model_used": report.model_used,
        "summary": report.summary,
        "issues_found": report.issues_found,
        "fixes_executed": report.fixes_executed,
        "fixes_succeeded": report.fixes_succeeded,
        "fixes_failed": report.fixes_failed,
        "requires_human_approval": report.requires_human_approval,
        "duration_seconds": report.duration_seconds,
        "actions": [
            {
                "action_name": a.action_name,
                "description": a.description,
                "severity": a.severity,
                "risk_level": a.risk_level,
                "status": a.status,
                "result": a.result,
            }
            for a in report.actions
        ],
    }


@router.get("/orchestrator/history")
async def get_orchestrator_history(
    request: Request,
    limit: int = 20,
) -> dict[str, Any]:
    """Get history of AI auto-fix runs."""
    orchestrator = getattr(request.app.state, "autofix_orchestrator", None)
    if orchestrator is None:
        return {"history": [], "total": 0}

    history = orchestrator.get_history(limit=limit)
    return {"history": history, "total": len(history)}


@router.post("/orchestrator/diagnose-only")
async def diagnose_only(request: Request) -> dict[str, Any]:
    """Run diagnosis without executing any fixes.

    Safe to run at any time — only analyzes system state.
    """
    admin_ip = request.client.host if request.client else "unknown"

    orchestrator = getattr(request.app.state, "autofix_orchestrator", None)
    if orchestrator is None:
        from config.settings import settings
        from src.services.autofix_orchestrator import AutoFixOrchestrator

        orchestrator = AutoFixOrchestrator(
            project_id=settings.gcp_project_id,
            model_name=settings.vertex_ai_model.replace("flash", "pro")
            if "flash" in settings.vertex_ai_model
            else "gemini-3.0-pro",
            region=settings.vertex_ai_location,
        )
        request.app.state.autofix_orchestrator = orchestrator

    _record_audit("orchestrator_diagnose", "Diagnosis-only run", admin_ip)

    report = await orchestrator.diagnose_and_fix(
        app_state=request.app.state,
        execute_fixes=False,
    )

    return {
        "report_id": report.report_id,
        "diagnosis_source": report.diagnosis_source,
        "summary": report.summary,
        "issues_found": report.issues_found,
        "requires_human_approval": report.requires_human_approval,
        "actions": [
            {
                "action_name": a.action_name,
                "description": a.description,
                "severity": a.severity,
                "risk_level": a.risk_level,
            }
            for a in report.actions
        ],
    }


# ---------------------------------------------------------------------------
# Claude CLI Bridge Endpoints (Device Login + Auto-Fix from GUI)
# ---------------------------------------------------------------------------


def _get_claude_bridge(request: Request):
    """Get or create the Claude CLI bridge instance."""
    bridge = getattr(request.app.state, "claude_cli_bridge", None)
    if bridge is None:
        from src.services.claude_cli_bridge import ClaudeCLIBridge

        bridge = ClaudeCLIBridge()
        request.app.state.claude_cli_bridge = bridge
    return bridge


@router.get("/claude/status")
async def claude_cli_status(request: Request) -> dict[str, Any]:
    """Get Claude CLI installation and authentication status.

    Returns whether Claude is installed, authenticated, and any
    pending device login information.
    """
    admin_ip = request.client.host if request.client else "unknown"
    _record_audit("claude_status_check", "Claude CLI status checked", admin_ip)

    bridge = _get_claude_bridge(request)
    auth_status = await bridge.check_auth_status()
    summary = bridge.get_status_summary()

    return {
        **auth_status,
        "summary": summary,
    }


@router.post("/claude/auth")
async def claude_cli_start_auth(request: Request) -> dict[str, Any]:
    """Start Claude CLI device-code authentication flow.

    Initiates ``claude auth login`` and returns the device code + URL.
    The super admin must visit the URL and enter the code to authorize
    this server's Claude CLI installation.

    This enables running Claude auto-fix directly from the admin panel
    without SSH access to the VM.
    """
    admin_ip = request.client.host if request.client else "unknown"
    _record_audit(
        "claude_auth_started",
        "Claude CLI device login initiated from admin panel",
        admin_ip,
    )

    bridge = _get_claude_bridge(request)

    try:
        result = await bridge.start_auth_login()
    except Exception:
        logger.exception("admin.claude_auth_failed")
        raise HTTPException(status_code=500, detail="Claude CLI authentication failed.")

    safe_result = {
        "success": result.get("success", False),
        "device_code": result.get("device_code"),
        "verification_url": result.get("verification_url"),
        "message": result.get("message", ""),
    }

    if safe_result.get("success"):
        _record_audit(
            "claude_auth_flow",
            f"Device code: {safe_result.get('device_code', 'N/A')}, "
            f"URL: {safe_result.get('verification_url', 'N/A')}",
            admin_ip,
        )

    return safe_result


@router.post("/claude/run")
async def claude_cli_run_autofix(
    body: ClaudeRunRequest,
    request: Request,
) -> dict[str, Any]:
    """Run Claude CLI auto-fix from the admin panel.

    Executes Claude CLI with the specified prompt (or default auto-fix
    prompt) and returns the session results.

    Requires Claude CLI to be installed and authenticated.
    """
    admin_ip = request.client.host if request.client else "unknown"

    bridge = _get_claude_bridge(request)

    # Check auth first
    auth = await bridge.check_auth_status()
    if not auth.get("installed"):
        raise HTTPException(
            status_code=503,
            detail="Claude CLI is not installed on this server. "
            "Re-deploy with --with-claude flag.",
        )

    _record_audit(
        "claude_run_started",
        f"Claude CLI auto-fix started (timeout={body.timeout}s)",
        admin_ip,
    )

    try:
        result = await bridge.run_autofix(
            prompt=body.prompt,
            timeout=body.timeout,
        )
    except Exception:
        logger.exception("admin.claude_run_failed")
        raise HTTPException(status_code=500, detail="Claude CLI auto-fix execution failed.")

    session = result.get("session", {})
    _record_audit(
        "claude_run_completed",
        f"Session {session.get('session_id')}: {session.get('status')} "
        f"({session.get('duration_seconds', 0)}s, "
        f"exit={session.get('exit_code')})",
        admin_ip,
    )

    # Return only safe fields, excluding any raw error traces
    safe_session = {
        "session_id": session.get("session_id"),
        "status": session.get("status"),
        "duration_seconds": session.get("duration_seconds"),
        "exit_code": session.get("exit_code"),
    }
    return {
        "success": result.get("success", False),
        "session": safe_session,
        "message": result.get("message", ""),
    }


@router.get("/claude/history")
async def claude_cli_history(
    request: Request,
    limit: int = 20,
) -> dict[str, Any]:
    """Get Claude CLI session history."""
    bridge = _get_claude_bridge(request)
    history = bridge.get_history(limit=limit)
    return {"history": history, "total": len(history)}


@router.get("/claude/session/{session_id}")
async def claude_cli_session_detail(
    session_id: str,
    request: Request,
) -> dict[str, Any]:
    """Get details of a specific Claude CLI session."""
    bridge = _get_claude_bridge(request)
    session = bridge.get_session(session_id)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail=f"Session '{session_id}' not found.",
        )
    return session


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def is_maintenance_mode_active() -> bool:
    """Check if maintenance mode is currently active.

    Used by middleware to gate non-admin requests.
    """
    return _maintenance_mode.get("enabled", False)


def get_maintenance_message() -> str:
    """Get the current maintenance mode message."""
    return _maintenance_mode.get(
        "message",
        "System is undergoing scheduled maintenance. Please try again shortly.",
    )
