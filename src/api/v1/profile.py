"""User profile and family eligibility API endpoints for HaqSetu v1.

Provides endpoints for:
    * Creating and updating family profiles (with DPDPA consent)
    * Running family-level eligibility checks (the KILLER FEATURE)
    * Retrieving scheme notifications
    * Deleting profiles (DPDPA right to erasure)

These endpoints power HaqSetu's unique family-based scheme discovery
-- no other Indian platform matches schemes for an entire family in
a single call.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from src.models.scheme import SchemeDocument
from src.models.user_profile import FamilyMember, UserProfile
from src.services.eligibility import (
    EligibilityEngine,
    EligibilityResult,
    FamilyEligibilityReport,
)
from src.services.notifications import Notification, NotificationService

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

router = APIRouter(prefix="/profile", tags=["profile"])


# ---------------------------------------------------------------------------
# In-memory profile store (production: use PostgreSQL / DynamoDB)
# ---------------------------------------------------------------------------

_profiles: dict[str, UserProfile] = {}


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class CreateProfileRequest(BaseModel):
    """Request body for creating or updating a user profile."""

    age: int | None = None
    gender: str | None = None
    state: str | None = None
    district: str | None = None
    pin_code: str | None = None
    annual_income: float | None = None
    is_bpl: bool | None = None
    category: str | None = None
    occupation: str | None = None
    land_holding_acres: float | None = None
    family_members: list[FamilyMember] = Field(default_factory=list)
    has_aadhaar: bool = True
    has_bank_account: bool | None = None
    has_ration_card: bool | None = None
    has_land_records: bool | None = None
    has_income_certificate: bool | None = None
    has_caste_certificate: bool | None = None
    has_domicile_certificate: bool | None = None
    preferred_language: str = "hi"
    preferred_channel: str = "web"
    consent_given: bool = False


class ProfileResponse(BaseModel):
    """Response containing the user profile."""

    profile_id: str
    age: int | None = None
    gender: str | None = None
    state: str | None = None
    district: str | None = None
    occupation: str | None = None
    category: str | None = None
    annual_income: float | None = None
    is_bpl: bool | None = None
    family_size: int
    family_members: list[dict[str, Any]]
    preferred_language: str
    consent_given: bool
    created_at: str
    updated_at: str


class EligibilityResponse(BaseModel):
    """Response containing the family eligibility report."""

    profile_id: str
    total_schemes_matched: int
    total_estimated_annual_benefit: str | None = None
    member_results: dict[str, list[dict[str, Any]]]
    top_priority_schemes: list[dict[str, Any]]
    missing_documents_summary: list[str]
    next_steps: list[str]
    generated_at: str


class NotificationListResponse(BaseModel):
    """Response containing user notifications."""

    profile_id: str
    notifications: list[dict[str, Any]]
    total: int


class DeleteProfileResponse(BaseModel):
    """Response confirming profile deletion."""

    profile_id: str
    deleted: bool
    message: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("", response_model=ProfileResponse, status_code=201)
async def create_profile(
    body: CreateProfileRequest,
    request: Request,
) -> ProfileResponse:
    """Create or update a user profile with family members.

    This is the entry point for HaqSetu's family-based scheme matching.
    Users provide their own details plus information about family members
    (spouse, children, parents, siblings).

    DPDPA compliance: Profile data is stored only if ``consent_given``
    is True.  All fields except ``consent_given`` are optional.
    """
    if not body.consent_given:
        raise HTTPException(
            status_code=400,
            detail=(
                "Consent is required to create a profile. "
                "Please set consent_given=true to proceed. "
                "Your data is protected under the Digital Personal Data Protection Act (DPDPA)."
            ),
        )

    profile = UserProfile(
        age=body.age,
        gender=body.gender,
        state=body.state,
        district=body.district,
        pin_code=body.pin_code,
        annual_income=body.annual_income,
        is_bpl=body.is_bpl,
        category=body.category,
        occupation=body.occupation,
        land_holding_acres=body.land_holding_acres,
        family_members=body.family_members,
        has_aadhaar=body.has_aadhaar,
        has_bank_account=body.has_bank_account,
        has_ration_card=body.has_ration_card,
        has_land_records=body.has_land_records,
        has_income_certificate=body.has_income_certificate,
        has_caste_certificate=body.has_caste_certificate,
        has_domicile_certificate=body.has_domicile_certificate,
        preferred_language=body.preferred_language,
        preferred_channel=body.preferred_channel,
        consent_given=True,
        consent_timestamp=datetime.now(UTC),
    )

    _profiles[profile.profile_id] = profile

    logger.info(
        "api.profile.created",
        profile_id=profile.profile_id,
        family_size=profile.family_size,
        state=profile.state,
    )

    return _profile_to_response(profile)


@router.get("/{profile_id}", response_model=ProfileResponse)
async def get_profile(profile_id: str) -> ProfileResponse:
    """Get a user profile by ID.

    Returns the full profile including all family member details
    and computed properties (family_size, has_children, etc.).
    """
    profile = _profiles.get(profile_id)
    if profile is None:
        raise HTTPException(
            status_code=404,
            detail=f"Profile '{profile_id}' not found.",
        )

    return _profile_to_response(profile)


@router.put("/{profile_id}", response_model=ProfileResponse)
async def update_profile(
    profile_id: str,
    body: CreateProfileRequest,
    request: Request,
) -> ProfileResponse:
    """Update an existing user profile.

    Replaces all profile fields with the provided values.
    Consent must still be given.
    """
    existing = _profiles.get(profile_id)
    if existing is None:
        raise HTTPException(
            status_code=404,
            detail=f"Profile '{profile_id}' not found.",
        )

    if not body.consent_given:
        raise HTTPException(
            status_code=400,
            detail="Consent is required to update a profile.",
        )

    updated = UserProfile(
        profile_id=profile_id,
        age=body.age,
        gender=body.gender,
        state=body.state,
        district=body.district,
        pin_code=body.pin_code,
        annual_income=body.annual_income,
        is_bpl=body.is_bpl,
        category=body.category,
        occupation=body.occupation,
        land_holding_acres=body.land_holding_acres,
        family_members=body.family_members,
        has_aadhaar=body.has_aadhaar,
        has_bank_account=body.has_bank_account,
        has_ration_card=body.has_ration_card,
        has_land_records=body.has_land_records,
        has_income_certificate=body.has_income_certificate,
        has_caste_certificate=body.has_caste_certificate,
        has_domicile_certificate=body.has_domicile_certificate,
        preferred_language=body.preferred_language,
        preferred_channel=body.preferred_channel,
        consent_given=True,
        consent_timestamp=datetime.now(UTC),
        created_at=existing.created_at,
        updated_at=datetime.now(UTC),
    )

    _profiles[profile_id] = updated

    logger.info(
        "api.profile.updated",
        profile_id=profile_id,
        family_size=updated.family_size,
    )

    return _profile_to_response(updated)


@router.post("/{profile_id}/eligibility", response_model=EligibilityResponse)
async def check_family_eligibility(
    profile_id: str,
    request: Request,
) -> EligibilityResponse:
    """Run family-level eligibility check for a profile.

    THIS IS THE KILLER FEATURE.

    Takes a user profile (with family members) and matches EVERY member
    against ALL available government schemes.  Returns a comprehensive
    ``FamilyEligibilityReport`` with:

    * Eligible schemes for each family member
    * Top priority schemes across the entire family
    * Missing documents summary
    * Actionable next steps

    A family of 5 can discover 20-30 relevant schemes in a single call.
    No other platform in India provides this.
    """
    profile = _profiles.get(profile_id)
    if profile is None:
        raise HTTPException(
            status_code=404,
            detail=f"Profile '{profile_id}' not found.",
        )

    # Get all loaded schemes from app state
    scheme_data: list[SchemeDocument] = getattr(request.app.state, "scheme_data", [])

    if not scheme_data:
        raise HTTPException(
            status_code=503,
            detail="Scheme data is not loaded. Please try again later.",
        )

    # Build eligibility engine and run family match
    engine = EligibilityEngine(schemes=scheme_data)
    report = engine.match_family(profile)

    logger.info(
        "api.profile.eligibility_check",
        profile_id=profile_id,
        family_size=profile.family_size,
        schemes_matched=report.total_schemes_matched,
        estimated_benefit=report.total_estimated_annual_benefit,
    )

    return _report_to_response(report)


@router.get("/{profile_id}/notifications", response_model=NotificationListResponse)
async def get_notifications(
    profile_id: str,
    request: Request,
) -> NotificationListResponse:
    """Get scheme notifications for a user profile.

    Returns all notifications (new schemes, deadlines, status updates)
    that have been generated for this profile.

    Notifications are generated proactively when:
    * A new scheme is launched that the user qualifies for
    * A scheme deadline is approaching
    * A payment installment is expected
    * The user is missing documents for a high-priority scheme
    """
    profile = _profiles.get(profile_id)
    if profile is None:
        raise HTTPException(
            status_code=404,
            detail=f"Profile '{profile_id}' not found.",
        )

    # Get notifications from app state (if notification service is running)
    notification_service: NotificationService | None = getattr(
        request.app.state, "notification_service", None
    )

    notifications: list[Notification] = []
    if notification_service is not None:
        notifications = notification_service.get_notifications_for_profile(profile_id)

    notifications_out = [
        {
            "notification_id": n.notification_id,
            "scheme_id": n.scheme_id,
            "scheme_name": n.scheme_name,
            "type": n.notification_type,
            "message": n.message,
            "language": n.language,
            "priority": n.priority,
            "for_member": n.for_member,
            "created_at": n.created_at.isoformat(),
            "sent": n.sent,
        }
        for n in notifications
    ]

    return NotificationListResponse(
        profile_id=profile_id,
        notifications=notifications_out,
        total=len(notifications_out),
    )


@router.delete("/{profile_id}", response_model=DeleteProfileResponse)
async def delete_profile(
    profile_id: str,
    request: Request,
) -> DeleteProfileResponse:
    """Delete a user profile (DPDPA right to erasure).

    Permanently removes all profile data including family member
    information and associated notifications.  This action is
    irreversible.

    Complies with the Digital Personal Data Protection Act (DPDPA)
    Section 12: Right of Data Principal to request erasure.
    """
    profile = _profiles.pop(profile_id, None)
    if profile is None:
        raise HTTPException(
            status_code=404,
            detail=f"Profile '{profile_id}' not found.",
        )

    # Also clear any notifications for this profile
    notification_service: NotificationService | None = getattr(
        request.app.state, "notification_service", None
    )
    if notification_service is not None:
        # Remove notifications from queue
        notification_service._notification_queue = [
            n for n in notification_service._notification_queue
            if n.profile_id != profile_id
        ]

    logger.info(
        "api.profile.deleted",
        profile_id=profile_id,
        reason="dpdpa_right_to_erasure",
    )

    return DeleteProfileResponse(
        profile_id=profile_id,
        deleted=True,
        message=(
            "Profile and all associated data have been permanently deleted "
            "as per your DPDPA right to erasure."
        ),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _profile_to_response(profile: UserProfile) -> ProfileResponse:
    """Convert a UserProfile to an API response."""
    family_members_out = [
        {
            "relation": m.relation,
            "name": m.name,
            "age": m.age,
            "gender": m.gender,
            "occupation": m.occupation,
            "education": m.education,
            "disability": m.disability,
            "is_student": m.is_student,
            "is_pregnant": m.is_pregnant,
        }
        for m in profile.family_members
    ]

    return ProfileResponse(
        profile_id=profile.profile_id,
        age=profile.age,
        gender=profile.gender,
        state=profile.state,
        district=profile.district,
        occupation=profile.occupation,
        category=profile.category,
        annual_income=profile.annual_income,
        is_bpl=profile.is_bpl,
        family_size=profile.family_size,
        family_members=family_members_out,
        preferred_language=profile.preferred_language,
        consent_given=profile.consent_given,
        created_at=profile.created_at.isoformat(),
        updated_at=profile.updated_at.isoformat(),
    )


def _report_to_response(report: FamilyEligibilityReport) -> EligibilityResponse:
    """Convert a FamilyEligibilityReport to an API response."""
    member_results_out: dict[str, list[dict[str, Any]]] = {}
    for member_key, results in report.member_results.items():
        member_results_out[member_key] = [
            {
                "scheme_id": r.scheme_id,
                "scheme_name": r.scheme_name,
                "eligible": r.eligible,
                "confidence": r.confidence,
                "matched_criteria": r.matched_criteria,
                "missing_criteria": r.missing_criteria,
                "missing_documents": r.missing_documents,
                "priority_score": r.priority_score,
                "for_member": r.for_member,
                "estimated_benefit": r.estimated_benefit,
                "category": r.category,
                "helpline": r.helpline,
            }
            for r in results
        ]

    top_priority_out = [
        {
            "scheme_id": r.scheme_id,
            "scheme_name": r.scheme_name,
            "eligible": r.eligible,
            "confidence": r.confidence,
            "priority_score": r.priority_score,
            "for_member": r.for_member,
            "estimated_benefit": r.estimated_benefit,
            "category": r.category,
            "matched_criteria": r.matched_criteria,
        }
        for r in report.top_priority_schemes
    ]

    return EligibilityResponse(
        profile_id=report.profile_id,
        total_schemes_matched=report.total_schemes_matched,
        total_estimated_annual_benefit=report.total_estimated_annual_benefit,
        member_results=member_results_out,
        top_priority_schemes=top_priority_out,
        missing_documents_summary=report.missing_documents_summary,
        next_steps=report.next_steps,
        generated_at=report.generated_at.isoformat(),
    )
