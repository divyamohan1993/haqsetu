"""Admin API key authentication for protected endpoints.

Provides a FastAPI dependency that validates the ``X-Admin-API-Key``
header against the configured ``HAQSETU_ADMIN_API_KEY`` environment
variable.  Uses constant-time comparison to prevent timing attacks.
"""

from __future__ import annotations

import hmac

import structlog
from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader

from config.settings import settings

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

_api_key_header = APIKeyHeader(name="X-Admin-API-Key", auto_error=False)


async def require_admin_api_key(
    request: Request,
    api_key: str | None = Security(_api_key_header),
) -> str:
    """FastAPI dependency that enforces admin API key authentication.

    Returns the validated key on success; raises 401/403 on failure.

    Usage::

        @router.post("/admin/ingest", dependencies=[Depends(require_admin_api_key)])
        async def trigger_ingestion(...): ...
    """
    configured_key = settings.admin_api_key

    if not configured_key:
        # In development without a configured key, log a warning but allow access
        if not settings.is_production:
            logger.warning(
                "auth.admin_key_not_configured",
                note="Admin API key not set; allowing request in development mode",
            )
            return ""
        # In production, reject all requests if the key isn't configured
        logger.error("auth.admin_key_not_configured_production")
        raise HTTPException(
            status_code=503,
            detail="Admin authentication is not configured.",
        )

    if not api_key:
        logger.warning(
            "auth.missing_api_key",
            path=request.url.path,
            client_ip=request.client.host if request.client else "unknown",
        )
        raise HTTPException(
            status_code=401,
            detail="Missing X-Admin-API-Key header.",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    # Constant-time comparison to prevent timing attacks
    if not hmac.compare_digest(api_key.encode(), configured_key.encode()):
        logger.warning(
            "auth.invalid_api_key",
            path=request.url.path,
            client_ip=request.client.host if request.client else "unknown",
        )
        raise HTTPException(
            status_code=403,
            detail="Invalid API key.",
        )

    return api_key
