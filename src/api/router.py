"""Main API router combining all v1 route modules.

Aggregates all routers under the ``/api/v1`` prefix so the
FastAPI application only needs to include a single router.

Includes:
    * Core: query, schemes, profile, health, languages
    * Admin: ingestion, verification, feedback
    * Voice Agent: natural conversation with proactive rights detection
    * Document Scanner: OCR + plain-language explanation of government docs
    * Legal Rights: BNS section mapper, applicable law identifier
    * RTI Generator: automated RTI application drafting
    * Emergency SOS: immediate legal distress assistance
    * Grievance Tracker: cross-portal grievance management
    * Nearby Services: CSC, DLSA, office finder
    * Accessibility: ISL, screen reader, haptic, Braille support
    * Self-Sustaining: health checks, cost monitoring, auto-updates
"""

from __future__ import annotations

from fastapi import APIRouter

from src.api.v1 import (
    accessibility,
    admin_recovery,
    document_scanner,
    emergency,
    feedback,
    grievance,
    health,
    ingestion,
    languages,
    legal_rights,
    nearby,
    profile,
    query,
    rti,
    schemes,
    self_sustaining,
    verification,
    voice_agent,
)

api_router = APIRouter(prefix="/api/v1")

# -- Core sub-routers ------------------------------------------------------
api_router.include_router(query.router)
api_router.include_router(schemes.router)
api_router.include_router(profile.router)
api_router.include_router(health.router)
api_router.include_router(languages.router)
api_router.include_router(ingestion.router)
api_router.include_router(verification.router)
api_router.include_router(feedback.router)

# -- New feature sub-routers -----------------------------------------------
api_router.include_router(voice_agent.router)
api_router.include_router(document_scanner.router)
api_router.include_router(legal_rights.router)
api_router.include_router(rti.router)
api_router.include_router(emergency.router)
api_router.include_router(grievance.router)
api_router.include_router(nearby.router)
api_router.include_router(accessibility.router)
api_router.include_router(self_sustaining.router)
api_router.include_router(admin_recovery.router)
