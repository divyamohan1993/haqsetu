"""Emergency SOS and Legal Distress API endpoints for HaqSetu.

Provides immediate help in emergency situations by connecting users
with the right helplines, generating distress reports, and providing
safety guidance.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/emergency", tags=["emergency-sos"])


class EmergencyReportRequest(BaseModel):
    description: str = Field(
        ..., min_length=5, max_length=5000,
        description="Describe the emergency situation",
    )
    location: str = Field(default="", description="Location (city, area, or address)")
    state: str = Field(default="", description="State name")
    latitude: float | None = None
    longitude: float | None = None
    language: str = Field(default="hi")


class SafetyPlanRequest(BaseModel):
    situation: str = Field(
        ..., min_length=10, max_length=5000,
        description="Describe the unsafe situation",
    )
    language: str = Field(default="hi")


@router.post("/report")
async def report_emergency(
    body: EmergencyReportRequest, request: Request
) -> dict:
    """Report an emergency and get immediate help.

    Returns the most relevant helpline numbers, generates a distress
    report, and provides immediate safety guidance.

    CALL 112 (UNIVERSAL EMERGENCY) IF IN IMMEDIATE DANGER.
    """
    sos_service = getattr(request.app.state, "emergency_sos", None)
    if sos_service is None:
        # Even if service is down, return basic emergency info
        return {
            "emergency_numbers": [
                {"name": "Universal Emergency", "number": "112"},
                {"name": "Police", "number": "100"},
                {"name": "Women Helpline", "number": "181"},
                {"name": "Child Helpline", "number": "1098"},
                {"name": "Ambulance", "number": "108"},
            ],
            "message": "Call 112 for immediate emergency assistance.",
        }

    try:
        response = await sos_service.report_emergency(
            description=body.description,
            location=body.location,
            language=body.language,
        )
    except Exception:
        logger.error("api.emergency.report_failed", exc_info=True)
        return {
            "emergency_numbers": [
                {"name": "Universal Emergency", "number": "112"},
                {"name": "Police", "number": "100"},
                {"name": "Women Helpline", "number": "181"},
            ],
            "message": "Call 112 for immediate emergency assistance.",
        }

    return {
        "emergency_type": response.emergency_type,
        "severity": response.severity,
        "immediate_action": response.immediate_action,
        "emergency_contacts": [
            {"name": c.name, "number": c.number, "description": c.description}
            for c in response.contacts
        ],
        "safety_tips": response.safety_tips,
        "report_id": response.report_id,
        "message": "If you are in immediate danger, call 112 NOW.",
    }


@router.get("/contacts/{emergency_type}")
async def get_emergency_contacts(
    emergency_type: str,
    state: str = "all",
    request: Request = None,
) -> dict:
    """Get emergency contact numbers by type and state.

    Types: domestic_violence, child_abuse, sexual_assault, police,
    medical, legal_aid, cyber_crime, senior_citizen, disability,
    labor, accident, missing_person.
    """
    sos_service = getattr(request.app.state, "emergency_sos", None)
    if sos_service is None:
        raise HTTPException(status_code=503, detail="Emergency SOS service not available")

    contacts = sos_service.get_emergency_contacts(emergency_type, state)
    return {
        "emergency_type": emergency_type,
        "state": state,
        "contacts": [
            {
                "name": c.name,
                "number": c.number,
                "description": c.description,
                "available_24x7": c.available_24x7,
            }
            for c in contacts
        ],
    }


@router.post("/safety-plan")
async def generate_safety_plan(
    body: SafetyPlanRequest, request: Request
) -> dict:
    """Generate a personalized safety plan for an unsafe situation.

    Provides step-by-step guidance for:
    - Domestic violence situations
    - Workplace harassment
    - Stalking and threats
    - Child safety concerns
    """
    sos_service = getattr(request.app.state, "emergency_sos", None)
    if sos_service is None:
        raise HTTPException(status_code=503, detail="Emergency SOS service not available")

    try:
        plan = await sos_service.generate_safety_plan(body.situation)
    except Exception:
        logger.error("api.emergency.safety_plan_failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate safety plan") from None

    return {
        "situation_type": plan.situation_type,
        "immediate_steps": plan.immediate_steps,
        "medium_term_steps": plan.medium_term_steps,
        "resources": plan.resources,
        "legal_protections": plan.legal_protections,
        "helplines": plan.helplines,
        "important": "If you are in immediate danger, call 112.",
    }
