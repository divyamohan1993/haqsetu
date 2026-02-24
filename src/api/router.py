"""Main API router combining all v1 route modules.

Aggregates the query, schemes, health, languages, profile, and ingestion
routers under the ``/api/v1`` prefix so the FastAPI application only
needs to include a single router.
"""

from __future__ import annotations

from fastapi import APIRouter

from src.api.v1 import health, ingestion, languages, profile, query, schemes

api_router = APIRouter(prefix="/api/v1")

# -- Include sub-routers ---------------------------------------------------
api_router.include_router(query.router)
api_router.include_router(schemes.router)
api_router.include_router(profile.router)
api_router.include_router(health.router)
api_router.include_router(languages.router)
api_router.include_router(ingestion.router)
