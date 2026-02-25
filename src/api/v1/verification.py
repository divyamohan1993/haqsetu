"""Verification API endpoints for HaqSetu v1.

Provides endpoints for checking scheme verification status, viewing
the verification dashboard, and triggering verification runs.
These endpoints expose the verification trust scores and evidence
chain to the public, ensuring transparency.

IMPORTANT: Only official government documents (Gazette of India,
India Code, Parliament records, MyScheme.gov.in, data.gov.in) are
accepted as proof of scheme existence. All other sources carry
zero trust weight.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from pydantic import BaseModel, Field

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

router = APIRouter(prefix="/verification", tags=["verification"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class SchemeVerificationResponse(BaseModel):
    """Verification status and trust details for a single scheme."""

    scheme_id: str
    scheme_name: str
    status: str
    trust_score: float
    sources_confirmed: list[str]
    sources_checked: list[str]
    gazette_confirmed: bool
    act_confirmed: bool
    parliament_confirmed: bool
    evidence_chain: list[dict[str, Any]]
    last_verified: str | None
    notes: list[str]


class VerificationDashboardResponse(BaseModel):
    """Aggregated verification statistics for the public dashboard."""

    total_schemes: int
    verified: int
    partially_verified: int
    unverified: int
    disputed: int
    average_trust_score: float
    last_pipeline_run: str | None
    top_verified_schemes: list[dict[str, Any]]
    recently_verified: list[dict[str, Any]]
    source_health: dict[str, bool]


class VerificationTriggerResponse(BaseModel):
    """Response returned after triggering a verification run."""

    message: str
    schemes_queued: int
    estimated_sources: int


class SchemeChangelogResponse(BaseModel):
    """Change history for a single scheme."""

    scheme_id: str
    changes: list[dict[str, Any]]
    total: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_verification_engine(request: Request):
    """Retrieve the verification engine from app state, or raise 503."""
    engine = getattr(request.app.state, "verification_engine", None)
    if engine is None:
        raise HTTPException(
            status_code=503,
            detail="Verification engine not initialised.",
        )
    return engine


def _get_verification_results(request: Request) -> dict[str, Any]:
    """Retrieve cached verification results from app state."""
    return getattr(request.app.state, "verification_results", {})


def _require_admin_api_key(request: Request) -> None:
    """Enforce ``X-Admin-API-Key`` when a production key is configured.

    If ``settings.admin_api_key`` is empty (the default during local
    development), the check is skipped so the SPA and dev tools work
    without extra configuration.
    """
    from config.settings import settings

    configured_key = settings.admin_api_key
    if not configured_key:
        # No key configured — allow the request (dev/staging).
        return

    provided_key = request.headers.get("X-Admin-API-Key", "")
    if provided_key != configured_key:
        raise HTTPException(
            status_code=403,
            detail="Invalid or missing X-Admin-API-Key header.",
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/status/{scheme_id}", response_model=SchemeVerificationResponse)
async def get_verification_status(
    scheme_id: str,
    request: Request,
) -> SchemeVerificationResponse:
    """Get verification status for a single scheme.

    Returns the current trust score, confirmed sources, evidence chain,
    and notes for the requested scheme.  Data is sourced from the
    verification engine's cached results stored in app state.
    """
    results = _get_verification_results(request)

    result = results.get(scheme_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"No verification data found for scheme '{scheme_id}'.",
        )

    logger.info("api.verification.status", scheme_id=scheme_id)

    return SchemeVerificationResponse(
        scheme_id=result.get("scheme_id", scheme_id),
        scheme_name=result.get("scheme_name", ""),
        status=result.get("status", "unverified"),
        trust_score=result.get("trust_score", 0.0),
        sources_confirmed=result.get("sources_confirmed", []),
        sources_checked=result.get("sources_checked", []),
        gazette_confirmed=result.get("gazette_confirmed", False),
        act_confirmed=result.get("act_confirmed", False),
        parliament_confirmed=result.get("parliament_confirmed", False),
        evidence_chain=result.get("evidence_chain", []),
        last_verified=result.get("last_verified"),
        notes=result.get("notes", []),
    )


@router.get("/dashboard", response_model=VerificationDashboardResponse)
async def get_verification_dashboard(
    request: Request,
) -> VerificationDashboardResponse:
    """Public dashboard showing aggregated verification statistics.

    Includes counts by status, the average trust score, top verified
    schemes, recently verified schemes, and the health of each
    official source (Gazette, India Code, Parliament, MyScheme, data.gov.in).
    """
    results = _get_verification_results(request)
    engine = getattr(request.app.state, "verification_engine", None)

    all_results = list(results.values())

    # Aggregate counts
    verified = 0
    partially_verified = 0
    unverified = 0
    disputed = 0
    trust_scores: list[float] = []

    for r in all_results:
        status = r.get("status", "unverified")
        if status == "verified":
            verified += 1
        elif status == "partially_verified":
            partially_verified += 1
        elif status == "disputed":
            disputed += 1
        else:
            unverified += 1

        trust_scores.append(r.get("trust_score", 0.0))

    average_trust_score = (
        sum(trust_scores) / len(trust_scores) if trust_scores else 0.0
    )

    # Top verified schemes by trust score
    sorted_by_trust = sorted(
        all_results,
        key=lambda r: r.get("trust_score", 0.0),
        reverse=True,
    )
    top_verified = [
        {
            "scheme_id": r.get("scheme_id", ""),
            "scheme_name": r.get("scheme_name", ""),
            "trust_score": r.get("trust_score", 0.0),
            "status": r.get("status", "unverified"),
        }
        for r in sorted_by_trust[:10]
    ]

    # Recently verified schemes by last_verified timestamp
    recently_sorted = sorted(
        [r for r in all_results if r.get("last_verified") is not None],
        key=lambda r: r.get("last_verified", ""),
        reverse=True,
    )
    recently_verified = [
        {
            "scheme_id": r.get("scheme_id", ""),
            "scheme_name": r.get("scheme_name", ""),
            "trust_score": r.get("trust_score", 0.0),
            "last_verified": r.get("last_verified"),
        }
        for r in recently_sorted[:10]
    ]

    # Source health
    source_health: dict[str, bool] = {
        "gazette_of_india": False,
        "india_code": False,
        "parliament_records": False,
        "myscheme_gov_in": False,
        "data_gov_in": False,
    }
    if engine is not None:
        engine_health = getattr(engine, "source_health", None)
        if engine_health is not None:
            if isinstance(engine_health, dict):
                source_health.update(engine_health)
            elif callable(engine_health):
                try:
                    source_health.update(engine_health())
                except Exception:
                    logger.warning("api.verification.dashboard.source_health_failed", exc_info=True)

    # Last pipeline run
    last_pipeline_run: str | None = None
    if engine is not None:
        last_run = getattr(engine, "last_pipeline_run", None)
        if last_run is not None:
            last_pipeline_run = (
                last_run.isoformat() if hasattr(last_run, "isoformat") else str(last_run)
            )

    logger.info(
        "api.verification.dashboard",
        total_schemes=len(all_results),
        verified=verified,
        average_trust_score=round(average_trust_score, 4),
    )

    return VerificationDashboardResponse(
        total_schemes=len(all_results),
        verified=verified,
        partially_verified=partially_verified,
        unverified=unverified,
        disputed=disputed,
        average_trust_score=round(average_trust_score, 4),
        last_pipeline_run=last_pipeline_run,
        top_verified_schemes=top_verified,
        recently_verified=recently_verified,
        source_health=source_health,
    )


@router.get("/search", response_model=list[SchemeVerificationResponse])
async def search_verification_status(
    request: Request,
    status: str | None = Query(default=None, description="Filter by verification status"),
    min_trust_score: float | None = Query(
        default=None, ge=0.0, le=1.0, description="Minimum trust score"
    ),
    source: str | None = Query(
        default=None, description="Filter by confirmed source name"
    ),
    page: int = Query(default=1, ge=1, description="Page number"),
    page_size: int = Query(default=20, ge=1, le=100, description="Results per page"),
) -> list[SchemeVerificationResponse]:
    """Search schemes by verification status.

    Supports filtering by verification status (verified, partially_verified,
    unverified, disputed), minimum trust score, and confirmed source name.
    Results are paginated.
    """
    results = _get_verification_results(request)
    all_results = list(results.values())

    # Apply filters
    filtered = all_results

    if status is not None:
        filtered = [r for r in filtered if r.get("status") == status]

    if min_trust_score is not None:
        filtered = [
            r for r in filtered if r.get("trust_score", 0.0) >= min_trust_score
        ]

    if source is not None:
        source_lower = source.lower()
        filtered = [
            r
            for r in filtered
            if any(s.lower() == source_lower for s in r.get("sources_confirmed", []))
        ]

    # Paginate
    start = (page - 1) * page_size
    end = start + page_size
    page_results = filtered[start:end]

    logger.info(
        "api.verification.search",
        status_filter=status,
        min_trust_score=min_trust_score,
        source_filter=source,
        total_matches=len(filtered),
        page=page,
    )

    return [
        SchemeVerificationResponse(
            scheme_id=r.get("scheme_id", ""),
            scheme_name=r.get("scheme_name", ""),
            status=r.get("status", "unverified"),
            trust_score=r.get("trust_score", 0.0),
            sources_confirmed=r.get("sources_confirmed", []),
            sources_checked=r.get("sources_checked", []),
            gazette_confirmed=r.get("gazette_confirmed", False),
            act_confirmed=r.get("act_confirmed", False),
            parliament_confirmed=r.get("parliament_confirmed", False),
            evidence_chain=r.get("evidence_chain", []),
            last_verified=r.get("last_verified"),
            notes=r.get("notes", []),
        )
        for r in page_results
    ]


@router.post("/trigger", response_model=VerificationTriggerResponse)
async def trigger_verification(
    request: Request,
    background_tasks: BackgroundTasks,
    scheme_id: str | None = Query(
        default=None, description="Specific scheme ID to verify (omit to verify all unverified)"
    ),
    force: bool = Query(
        default=False, description="Re-verify even if recently verified"
    ),
) -> VerificationTriggerResponse:
    """Trigger a verification run for one scheme or all unverified schemes.

    When *scheme_id* is provided, only that scheme is queued for
    verification.  Otherwise, all schemes that are currently unverified
    (or all schemes when *force* is ``True``) are queued.

    Requires ``X-Admin-API-Key`` header when ``ADMIN_API_KEY`` is
    configured (typically production).  In development the check is
    skipped when the key is empty.

    Verification runs as a background task and results will appear in
    the dashboard and status endpoints once complete.
    """
    _require_admin_api_key(request)
    engine = _get_verification_engine(request)
    results = _get_verification_results(request)

    schemes_to_verify: list[str] = []

    if scheme_id is not None:
        # Verify a single scheme
        scheme_data = getattr(request.app.state, "scheme_data", [])
        found = any(s.scheme_id == scheme_id for s in scheme_data) or scheme_id in results
        if not found:
            raise HTTPException(
                status_code=404,
                detail=f"Scheme '{scheme_id}' not found.",
            )
        schemes_to_verify.append(scheme_id)
    else:
        # Queue all unverified (or all if force)
        scheme_data = getattr(request.app.state, "scheme_data", [])
        for s in scheme_data:
            if force:
                schemes_to_verify.append(s.scheme_id)
            else:
                existing = results.get(s.scheme_id)
                if existing is None or existing.get("status") == "unverified":
                    schemes_to_verify.append(s.scheme_id)

    if not schemes_to_verify:
        return VerificationTriggerResponse(
            message="No schemes require verification at this time.",
            schemes_queued=0,
            estimated_sources=0,
        )

    # Estimate the number of sources to check (5 official sources per scheme)
    estimated_sources = len(schemes_to_verify) * 5

    async def _run_verification() -> None:
        """Background task that runs verification for queued schemes."""
        try:
            for sid in schemes_to_verify:
                await engine.verify_scheme(sid)
            logger.info(
                "api.verification.trigger.completed",
                schemes_verified=len(schemes_to_verify),
            )
        except Exception:
            logger.error("api.verification.trigger.failed", exc_info=True)

    background_tasks.add_task(_run_verification)

    logger.info(
        "api.verification.trigger",
        scheme_id=scheme_id,
        force=force,
        schemes_queued=len(schemes_to_verify),
        estimated_sources=estimated_sources,
    )

    return VerificationTriggerResponse(
        message=(
            f"Verification queued for {len(schemes_to_verify)} scheme(s). "
            f"Results will be available shortly."
        ),
        schemes_queued=len(schemes_to_verify),
        estimated_sources=estimated_sources,
    )


@router.get("/changelog/{scheme_id}", response_model=SchemeChangelogResponse)
async def get_scheme_changelog(
    scheme_id: str,
    request: Request,
) -> SchemeChangelogResponse:
    """Return the change history for a scheme.

    Each entry in the changelog records what changed, when it changed,
    which source reported the change, and whether the change has been
    verified against official records.
    """
    engine = getattr(request.app.state, "verification_engine", None)

    changes: list[dict[str, Any]] = []

    if engine is not None:
        get_changelog = getattr(engine, "get_changelog", None)
        if get_changelog is not None:
            try:
                changes = await get_changelog(scheme_id)
            except Exception:
                logger.error(
                    "api.verification.changelog.failed",
                    scheme_id=scheme_id,
                    exc_info=True,
                )
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to retrieve changelog for scheme '{scheme_id}'.",
                )

    if not changes:
        # Check that the scheme exists at all
        results = _get_verification_results(request)
        scheme_data = getattr(request.app.state, "scheme_data", [])
        scheme_exists = (
            scheme_id in results
            or any(s.scheme_id == scheme_id for s in scheme_data)
        )
        if not scheme_exists:
            raise HTTPException(
                status_code=404,
                detail=f"Scheme '{scheme_id}' not found.",
            )

    logger.info(
        "api.verification.changelog",
        scheme_id=scheme_id,
        total_changes=len(changes),
    )

    return SchemeChangelogResponse(
        scheme_id=scheme_id,
        changes=changes,
        total=len(changes),
    )


@router.get("/evidence/{scheme_id}", response_model=dict[str, Any])
async def get_scheme_evidence(
    scheme_id: str,
    request: Request,
) -> dict[str, Any]:
    """Return the full evidence chain for a scheme.

    Includes all source documents, URLs, retrieval dates, and relevant
    excerpts used to determine the scheme's verification status and
    trust score.
    """
    results = _get_verification_results(request)

    result = results.get(scheme_id)
    if result is None:
        # Check scheme_data as fallback
        scheme_data = getattr(request.app.state, "scheme_data", [])
        scheme_exists = any(s.scheme_id == scheme_id for s in scheme_data)
        if not scheme_exists:
            raise HTTPException(
                status_code=404,
                detail=f"Scheme '{scheme_id}' not found.",
            )
        raise HTTPException(
            status_code=404,
            detail=f"No verification evidence found for scheme '{scheme_id}'. "
            f"Trigger verification first.",
        )

    # Build full evidence response
    engine = getattr(request.app.state, "verification_engine", None)

    evidence: dict[str, Any] = {
        "scheme_id": scheme_id,
        "scheme_name": result.get("scheme_name", ""),
        "status": result.get("status", "unverified"),
        "trust_score": result.get("trust_score", 0.0),
        "evidence_chain": result.get("evidence_chain", []),
        "sources_confirmed": result.get("sources_confirmed", []),
        "sources_checked": result.get("sources_checked", []),
        "gazette_confirmed": result.get("gazette_confirmed", False),
        "act_confirmed": result.get("act_confirmed", False),
        "parliament_confirmed": result.get("parliament_confirmed", False),
        "last_verified": result.get("last_verified"),
    }

    # Attempt to get extended evidence from the engine
    if engine is not None:
        get_evidence = getattr(engine, "get_full_evidence", None)
        if get_evidence is not None:
            try:
                extended = await get_evidence(scheme_id)
                if isinstance(extended, dict):
                    evidence.update(extended)
            except Exception:
                logger.warning(
                    "api.verification.evidence.extended_failed",
                    scheme_id=scheme_id,
                    exc_info=True,
                )

    logger.info("api.verification.evidence", scheme_id=scheme_id)

    return evidence
