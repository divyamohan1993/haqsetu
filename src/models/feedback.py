"""Feedback and grievance models for HaqSetu.

Enables citizens to report issues with scheme information accuracy,
submit grievances about scheme implementation, and provide feedback
on the platform. All feedback is tracked and can trigger re-verification.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field


class FeedbackType(StrEnum):
    __slots__ = ()

    ACCURACY_REPORT = "accuracy_report"
    MISSING_SCHEME = "missing_scheme"
    INCORRECT_INFO = "incorrect_info"
    SCHEME_EXPIRED = "scheme_expired"
    GRIEVANCE = "grievance"
    SUGGESTION = "suggestion"
    APPRECIATION = "appreciation"


class FeedbackPriority(StrEnum):
    __slots__ = ()

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FeedbackStatus(StrEnum):
    __slots__ = ()

    SUBMITTED = "submitted"
    UNDER_REVIEW = "under_review"
    VERIFIED = "verified"
    RESOLVED = "resolved"
    REJECTED = "rejected"


class CitizenFeedback(BaseModel):
    """A single citizen feedback or grievance report.

    Tracks the full lifecycle from submission through resolution,
    and can trigger re-verification of scheme information when
    accuracy issues are reported.
    """

    model_config = {"frozen": False}

    feedback_id: str = Field(default_factory=lambda: uuid4().hex)
    feedback_type: str
    priority: str = FeedbackPriority.MEDIUM
    scheme_id: str | None = None
    scheme_name: str | None = None
    description: str = Field(..., min_length=10, max_length=2000)
    expected_correction: str | None = None
    evidence_url: str | None = None
    language: str = "en"
    submitted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: str = FeedbackStatus.SUBMITTED
    resolution_notes: str | None = None
    triggered_reverification: bool = False


class FeedbackResponse(BaseModel):
    """Response returned after submitting feedback."""

    feedback_id: str
    message: str
    status: str


class FeedbackListResponse(BaseModel):
    """Paginated list of feedback entries."""

    feedbacks: list[dict]
    total: int
    page: int
    page_size: int


class FeedbackStats(BaseModel):
    """Aggregated statistics about feedback processing."""

    total_feedback: int
    pending_review: int
    resolved: int
    accuracy_reports: int
    grievances: int
    average_resolution_time_hours: float | None
