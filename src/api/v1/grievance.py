"""Grievance Tracker API endpoints for HaqSetu.

Provides endpoints for creating, tracking, and escalating
grievances across government portals.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/grievance", tags=["grievance-tracker"])


class GrievanceCreateRequest(BaseModel):
    complainant_name: str = Field(..., min_length=2, max_length=200)
    description: str = Field(..., min_length=20, max_length=5000)
    grievance_type: str = Field(
        ...,
        description="Type: public_service, corruption, delay, discrimination, other",
    )
    department: str = Field(default="", max_length=200)
    state: str = Field(default="", max_length=100)
    district: str = Field(default="", max_length=100)
    language: str = Field(default="hi")


@router.post("/create")
async def create_grievance(
    body: GrievanceCreateRequest, request: Request
) -> dict:
    """Create a grievance draft for filing on government portals.

    Generates a properly formatted grievance and provides guidance
    on where and how to file it (CPGRAMS, state portal, etc.).
    """
    grievance_service = getattr(request.app.state, "grievance_tracker", None)
    if grievance_service is None:
        raise HTTPException(status_code=503, detail="Grievance tracker not available")

    try:
        from src.services.grievance_tracker import GrievanceRequest
        greq = GrievanceRequest(
            complainant_name=body.complainant_name,
            description=body.description,
            grievance_type=body.grievance_type,
            department=body.department,
            state=body.state,
            district=body.district,
        )
        draft = await grievance_service.create_grievance(greq)
    except Exception:
        logger.error("api.grievance.create_failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create grievance") from None

    return {
        "grievance_id": draft.grievance_id,
        "formatted_complaint": draft.formatted_complaint,
        "recommended_portal": draft.recommended_portal,
        "portal_url": draft.portal_url,
        "filing_steps": draft.filing_steps,
        "expected_timeline": draft.expected_timeline,
        "escalation_info": draft.escalation_info,
    }


@router.get("/escalation-path/{grievance_type}")
async def get_escalation_path(
    grievance_type: str,
    authority_level: str = "central",
    request: Request = None,
) -> dict:
    """Get the escalation path for a grievance type.

    Shows the hierarchy of authorities to approach if your
    grievance is not resolved at the first level.
    """
    grievance_service = getattr(request.app.state, "grievance_tracker", None)
    if grievance_service is None:
        raise HTTPException(status_code=503, detail="Grievance tracker not available")

    path = grievance_service.get_escalation_path(grievance_type, authority_level)
    return {
        "grievance_type": grievance_type,
        "authority_level": authority_level,
        "levels": path.levels,
        "timelines": path.timelines,
        "tips": path.tips,
    }


@router.get("/portals")
async def get_grievance_portals(
    grievance_type: str = "general",
    state: str = "central",
    request: Request = None,
) -> dict:
    """Get information about grievance portals for filing complaints."""
    grievance_service = getattr(request.app.state, "grievance_tracker", None)
    if grievance_service is None:
        raise HTTPException(status_code=503, detail="Grievance tracker not available")

    portal = grievance_service.get_portal_info(grievance_type, state)
    return {
        "portal_name": portal.portal_name,
        "url": portal.url,
        "helpline": portal.helpline,
        "filing_steps": portal.filing_steps,
        "supported_types": portal.supported_types,
    }
