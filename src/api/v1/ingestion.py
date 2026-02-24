"""Admin ingestion API endpoints for HaqSetu v1.

Provides endpoints for triggering and monitoring the scheme data
ingestion pipeline.  All endpoints require admin-level access.

Endpoints
---------
- ``POST /api/v1/admin/ingest``          -- Trigger a full ingestion run.
- ``POST /api/v1/admin/ingest/incremental`` -- Trigger an incremental update.
- ``GET  /api/v1/admin/ingest/status``   -- Get the last ingestion result.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin/ingest", tags=["admin", "ingestion"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class IngestionTriggerResponse(BaseModel):
    """Response returned after triggering an ingestion run."""

    status: str
    message: str
    result: dict[str, Any] | None = None


class IngestionStatusResponse(BaseModel):
    """Response for the ingestion status endpoint."""

    status: str
    last_result: dict[str, Any] | None = None
    scheduler_running: bool = False
    last_full_run: str | None = None
    last_incremental_run: str | None = None


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _get_pipeline(request: Request):
    """Retrieve the ingestion pipeline from app state, or raise 503."""
    pipeline = getattr(request.app.state, "ingestion_pipeline", None)
    if pipeline is None:
        raise HTTPException(
            status_code=503,
            detail="Ingestion pipeline not initialised.",
        )
    return pipeline


def _get_scheduler(request: Request):
    """Retrieve the scheduler from app state (may be None)."""
    return getattr(request.app.state, "scheduler", None)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("", response_model=IngestionTriggerResponse)
async def trigger_full_ingestion(
    request: Request,
) -> IngestionTriggerResponse:
    """Trigger a full scheme ingestion from all sources.

    This will:
    1. Fetch all schemes from MyScheme.gov.in.
    2. Fetch supplementary data from data.gov.in.
    3. Merge with bundled seed data.
    4. Deduplicate, validate, and save.

    **Note:** This is a long-running operation and may take several
    minutes depending on the number of schemes and network conditions.
    """
    pipeline = _get_pipeline(request)

    logger.info("api.admin.ingest.full_triggered")

    try:
        result = await pipeline.run_full_ingestion()
        return IngestionTriggerResponse(
            status="completed",
            message=(
                f"Full ingestion completed. Fetched {result.total_fetched} schemes "
                f"({result.new_schemes} new, {result.updated_schemes} updated, "
                f"{result.failed_schemes} failed) in {result.duration_seconds:.1f}s."
            ),
            result=result.to_dict(),
        )
    except Exception as exc:
        logger.error("api.admin.ingest.full_failed", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Ingestion failed: {exc}",
        ) from exc


@router.post("/incremental", response_model=IngestionTriggerResponse)
async def trigger_incremental_ingestion(
    request: Request,
) -> IngestionTriggerResponse:
    """Trigger an incremental scheme data update.

    Only fetches and updates schemes that have changed since the last
    ingestion run.  Much faster than a full ingestion.
    """
    pipeline = _get_pipeline(request)

    logger.info("api.admin.ingest.incremental_triggered")

    try:
        result = await pipeline.run_incremental_update()
        return IngestionTriggerResponse(
            status="completed",
            message=(
                f"Incremental update completed. {result.new_schemes} new, "
                f"{result.updated_schemes} updated, {result.failed_schemes} failed "
                f"in {result.duration_seconds:.1f}s."
            ),
            result=result.to_dict(),
        )
    except Exception as exc:
        logger.error("api.admin.ingest.incremental_failed", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Incremental ingestion failed: {exc}",
        ) from exc


@router.get("/status", response_model=IngestionStatusResponse)
async def get_ingestion_status(
    request: Request,
) -> IngestionStatusResponse:
    """Get the status and result of the last ingestion run.

    Returns information about:
    - The last ingestion result (counts, errors, duration).
    - Whether the background scheduler is running.
    - Timestamps of the last full and incremental runs.
    """
    pipeline = getattr(request.app.state, "ingestion_pipeline", None)
    scheduler = _get_scheduler(request)

    last_result: dict[str, Any] | None = None

    # Try to get the last result from the pipeline
    if pipeline is not None and pipeline.last_result is not None:
        last_result = pipeline.last_result.to_dict()

    # Fall back to cached result
    if last_result is None:
        cache = getattr(request.app.state, "cache", None)
        if cache is not None:
            cached = await cache.get("ingestion:last_result")
            if cached is not None:
                last_result = cached

    scheduler_running = scheduler is not None and scheduler.is_running
    last_full = None
    last_incremental = None

    if scheduler is not None:
        if scheduler.last_full_run is not None:
            last_full = scheduler.last_full_run.isoformat()
        if scheduler.last_incremental_run is not None:
            last_incremental = scheduler.last_incremental_run.isoformat()

    status = "ready" if pipeline is not None else "not_initialised"

    return IngestionStatusResponse(
        status=status,
        last_result=last_result,
        scheduler_running=scheduler_running,
        last_full_run=last_full,
        last_incremental_run=last_incremental,
    )
