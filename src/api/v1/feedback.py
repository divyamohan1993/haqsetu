"""Citizen feedback and grievance API endpoints for HaqSetu v1.

Provides endpoints for submitting, tracking, and managing citizen
feedback about scheme information accuracy, grievances about scheme
implementation, and general platform feedback.

Feedback that reports accuracy issues can trigger automatic
re-verification of the affected scheme data.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from src.models.feedback import (
    CitizenFeedback,
    FeedbackListResponse,
    FeedbackResponse,
    FeedbackStats,
    FeedbackStatus,
    FeedbackType,
)

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

router = APIRouter(prefix="/feedback", tags=["feedback"])


# ---------------------------------------------------------------------------
# In-memory feedback store (production: use PostgreSQL / DynamoDB)
# ---------------------------------------------------------------------------

_feedback_store: dict[str, CitizenFeedback] = {}
_feedback_index: list[str] = []


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


def _validate_gov_url(url: str | None) -> str | None:
    """Validate that evidence URLs point to known government domains only.

    Prevents open redirect and SSRF attacks by restricting evidence URLs
    to a whitelist of official Indian government domains.
    """
    if url is None or url.strip() == "":
        return None
    import re
    from urllib.parse import urlparse

    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None
    host = (parsed.hostname or "").lower()
    gov_patterns = [
        r"\.gov\.in$",
        r"\.nic\.in$",
        r"\.india\.gov\.in$",
        r"^sansad\.in$",
        r"^indiacode\.nic\.in$",
        r"^egazette\.gov\.in$",
        r"^myscheme\.gov\.in$",
        r"^data\.gov\.in$",
    ]
    if any(re.search(pat, host) for pat in gov_patterns):
        return url
    return None


class SubmitFeedbackRequest(BaseModel):
    """Request body for submitting citizen feedback."""

    feedback_type: str = Field(
        ...,
        description=(
            "Type of feedback: accuracy_report, missing_scheme, "
            "incorrect_info, scheme_expired, grievance, suggestion, appreciation"
        ),
    )
    scheme_id: str | None = Field(default=None, max_length=100, description="ID of the scheme this feedback is about")
    scheme_name: str | None = Field(default=None, max_length=500, description="Name of the scheme this feedback is about")
    description: str = Field(..., min_length=10, max_length=2000, description="Detailed feedback description")
    expected_correction: str | None = Field(default=None, max_length=2000, description="What the citizen believes the correct information is")
    evidence_url: str | None = Field(default=None, max_length=500, description="Government URL (.gov.in/.nic.in) as evidence")
    language: str = Field(default="en", max_length=10, description="Language code for the feedback")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("", response_model=FeedbackResponse, status_code=201)
async def submit_feedback(
    body: SubmitFeedbackRequest,
    request: Request,
) -> FeedbackResponse:
    """Submit new citizen feedback or grievance.

    Citizens can report accuracy issues, missing schemes, incorrect
    information, expired schemes, grievances, suggestions, or
    appreciation.

    If the feedback type is ``accuracy_report`` or ``incorrect_info``,
    the affected scheme is automatically flagged for re-verification.
    """
    # Validate feedback type
    try:
        FeedbackType(body.feedback_type)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid feedback_type '{body.feedback_type}'. "
                f"Valid types: {[t.value for t in FeedbackType]}"
            ),
        )

    # Sanitise evidence URL: only accept official government domains
    safe_evidence_url = _validate_gov_url(body.evidence_url)
    if body.evidence_url and safe_evidence_url is None:
        logger.warning(
            "api.feedback.rejected_evidence_url",
            original_url=body.evidence_url[:120],
        )

    feedback = CitizenFeedback(
        feedback_type=body.feedback_type,
        scheme_id=body.scheme_id,
        scheme_name=body.scheme_name,
        description=body.description,
        expected_correction=body.expected_correction,
        evidence_url=safe_evidence_url,
        language=body.language,
    )

    # Auto-flag for re-verification on accuracy-related reports
    triggered_reverification = False
    if body.feedback_type in (
        FeedbackType.ACCURACY_REPORT,
        FeedbackType.INCORRECT_INFO,
    ):
        feedback.triggered_reverification = True
        triggered_reverification = True

        logger.info(
            "api.feedback.reverification_flagged",
            feedback_id=feedback.feedback_id,
            feedback_type=body.feedback_type,
            scheme_id=body.scheme_id,
        )

    # Store in memory
    _feedback_store[feedback.feedback_id] = feedback
    _feedback_index.insert(0, feedback.feedback_id)

    # Persist to cache if available
    cache = getattr(request.app.state, "cache", None)
    if cache is not None:
        try:
            await cache.set(
                f"feedback:{feedback.feedback_id}",
                feedback.model_dump(mode="json"),
                ttl_seconds=86400 * 30,  # 30 days
            )
            await cache.set("feedback:index", _feedback_index)
        except Exception:
            logger.warning("api.feedback.cache_write_failed", exc_info=True)

    logger.info(
        "api.feedback.submitted",
        feedback_id=feedback.feedback_id,
        feedback_type=body.feedback_type,
        scheme_id=body.scheme_id,
        triggered_reverification=triggered_reverification,
    )

    message = "Thank you for your feedback. It has been recorded and will be reviewed."
    if triggered_reverification:
        message += " This report has been flagged for re-verification of the scheme information."

    return FeedbackResponse(
        feedback_id=feedback.feedback_id,
        message=message,
        status=feedback.status,
    )


@router.get("/stats", response_model=FeedbackStats)
async def get_feedback_stats(
    request: Request,
) -> FeedbackStats:
    """Get public statistics about feedback processing.

    Returns aggregate counts of feedback by type and status,
    useful for transparency and accountability.
    """
    all_feedback = list(_feedback_store.values())

    total = len(all_feedback)
    pending = sum(
        1 for f in all_feedback
        if f.status in (FeedbackStatus.SUBMITTED, FeedbackStatus.UNDER_REVIEW)
    )
    resolved = sum(1 for f in all_feedback if f.status == FeedbackStatus.RESOLVED)
    accuracy_reports = sum(
        1 for f in all_feedback
        if f.feedback_type == FeedbackType.ACCURACY_REPORT
    )
    grievances = sum(
        1 for f in all_feedback
        if f.feedback_type == FeedbackType.GRIEVANCE
    )

    # Calculate average resolution time for resolved feedback
    avg_resolution: float | None = None
    resolved_feedback = [f for f in all_feedback if f.status == FeedbackStatus.RESOLVED]
    if resolved_feedback:
        total_hours = 0.0
        count = 0
        now = datetime.now(UTC)
        for f in resolved_feedback:
            delta = now - f.submitted_at
            total_hours += delta.total_seconds() / 3600
            count += 1
        avg_resolution = round(total_hours / count, 2) if count > 0 else None

    logger.info(
        "api.feedback.stats_requested",
        total=total,
        pending=pending,
        resolved=resolved,
    )

    return FeedbackStats(
        total_feedback=total,
        pending_review=pending,
        resolved=resolved,
        accuracy_reports=accuracy_reports,
        grievances=grievances,
        average_resolution_time_hours=avg_resolution,
    )


@router.get("/{feedback_id}")
async def get_feedback(
    feedback_id: str,
    request: Request,
) -> dict[str, Any]:
    """Get the status and full details of a specific feedback.

    Returns the complete feedback record including resolution status,
    notes, and whether re-verification was triggered.
    """
    feedback = _feedback_store.get(feedback_id)

    # Fall back to cache if not in memory
    if feedback is None:
        cache = getattr(request.app.state, "cache", None)
        if cache is not None:
            try:
                cached = await cache.get(f"feedback:{feedback_id}")
                if cached is not None:
                    feedback = CitizenFeedback(**cached)
            except Exception:
                logger.warning("api.feedback.cache_read_failed", exc_info=True)

    if feedback is None:
        raise HTTPException(
            status_code=404,
            detail=f"Feedback '{feedback_id}' not found.",
        )

    return {
        "feedback_id": feedback.feedback_id,
        "feedback_type": feedback.feedback_type,
        "priority": feedback.priority,
        "scheme_id": feedback.scheme_id,
        "scheme_name": feedback.scheme_name,
        "description": feedback.description,
        "expected_correction": feedback.expected_correction,
        "evidence_url": feedback.evidence_url,
        "language": feedback.language,
        "submitted_at": feedback.submitted_at.isoformat(),
        "status": feedback.status,
        "resolution_notes": feedback.resolution_notes,
        "triggered_reverification": feedback.triggered_reverification,
    }


@router.get("", response_model=FeedbackListResponse)
async def list_feedback(
    request: Request,
    feedback_type: str | None = Query(default=None, description="Filter by feedback type"),
    scheme_id: str | None = Query(default=None, description="Filter by scheme ID"),
    status: str | None = Query(default=None, description="Filter by status"),
    page: int = Query(default=1, ge=1, description="Page number"),
    page_size: int = Query(default=20, ge=1, le=100, description="Results per page"),
) -> FeedbackListResponse:
    """List feedback with optional filters.

    Supports filtering by feedback type, scheme ID, and status.
    Results are sorted by submission date descending (most recent first).
    """
    # Validate feedback_type filter if provided
    if feedback_type is not None:
        try:
            FeedbackType(feedback_type)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Invalid feedback_type '{feedback_type}'. "
                    f"Valid types: {[t.value for t in FeedbackType]}"
                ),
            )

    # Validate status filter if provided
    if status is not None:
        try:
            FeedbackStatus(status)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Invalid status '{status}'. "
                    f"Valid statuses: {[s.value for s in FeedbackStatus]}"
                ),
            )

    # Build filtered list from index (already sorted by submitted_at descending)
    filtered: list[CitizenFeedback] = []
    for fid in _feedback_index:
        fb = _feedback_store.get(fid)
        if fb is None:
            continue

        if feedback_type is not None and fb.feedback_type != feedback_type:
            continue
        if scheme_id is not None and fb.scheme_id != scheme_id:
            continue
        if status is not None and fb.status != status:
            continue

        filtered.append(fb)

    total = len(filtered)

    # Paginate
    start = (page - 1) * page_size
    end = start + page_size
    page_feedback = filtered[start:end]

    feedbacks_out = [
        {
            "feedback_id": f.feedback_id,
            "feedback_type": f.feedback_type,
            "priority": f.priority,
            "scheme_id": f.scheme_id,
            "scheme_name": f.scheme_name,
            "description": f.description[:200] if f.description else "",
            "submitted_at": f.submitted_at.isoformat(),
            "status": f.status,
            "triggered_reverification": f.triggered_reverification,
        }
        for f in page_feedback
    ]

    return FeedbackListResponse(
        feedbacks=feedbacks_out,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/{feedback_id}/verify")
async def trigger_reverification(
    feedback_id: str,
    request: Request,
) -> dict[str, Any]:
    """Mark feedback as triggering re-verification of scheme data.

    Sets ``triggered_reverification`` to True and queues the
    associated scheme for re-verification through the ingestion
    pipeline.
    """
    feedback = _feedback_store.get(feedback_id)

    if feedback is None:
        raise HTTPException(
            status_code=404,
            detail=f"Feedback '{feedback_id}' not found.",
        )

    feedback.triggered_reverification = True
    feedback.status = FeedbackStatus.UNDER_REVIEW

    # Persist updated feedback to cache
    cache = getattr(request.app.state, "cache", None)
    if cache is not None:
        try:
            await cache.set(
                f"feedback:{feedback.feedback_id}",
                feedback.model_dump(mode="json"),
                ttl_seconds=86400 * 30,
            )
        except Exception:
            logger.warning("api.feedback.cache_write_failed", exc_info=True)

    # Queue scheme for re-verification if scheme_id is available
    if feedback.scheme_id:
        verification_engine = getattr(request.app.state, "verification_engine", None)
        if verification_engine is not None:
            try:
                await verification_engine.verify_scheme(feedback.scheme_id)
                logger.info(
                    "api.feedback.scheme_reverification_queued",
                    feedback_id=feedback_id,
                    scheme_id=feedback.scheme_id,
                )
            except Exception:
                logger.warning("api.feedback.reverification_queue_failed", exc_info=True)

    logger.info(
        "api.feedback.reverification_triggered",
        feedback_id=feedback_id,
        scheme_id=feedback.scheme_id,
    )

    return {
        "feedback_id": feedback.feedback_id,
        "status": feedback.status,
        "triggered_reverification": feedback.triggered_reverification,
        "message": "Scheme has been queued for re-verification.",
    }
