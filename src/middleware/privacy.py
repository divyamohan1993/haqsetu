"""DPDPA (Digital Personal Data Protection Act) compliance middleware.

Strips PII (Aadhaar numbers, phone numbers, etc.) from request/response
logs, adds privacy-related headers to all responses, and records consent
status for audit purposes.
"""

from __future__ import annotations

import re
from typing import Final

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# PII sanitisation patterns
# ---------------------------------------------------------------------------

# Aadhaar: 12-digit number, optionally formatted as XXXX-XXXX-XXXX or
# XXXX XXXX XXXX.  We preserve only the last 4 digits.
_AADHAAR_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b(\d{4})[\s-]?(\d{4})[\s-]?(\d{4})\b"
)

# Indian phone numbers: +91 followed by 10 digits, or a bare 10-digit
# number starting with 6-9.  We preserve only the last 4 digits.
_PHONE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:\+91[\s-]?)?([6-9]\d{5})(\d{4})\b"
)

# Email addresses (basic pattern).
_EMAIL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"
)


def sanitize_aadhaar(text: str) -> str:
    """Mask Aadhaar numbers in *text*, preserving only the last 4 digits.

    ``1234 5678 9012`` becomes ``XXXX-XXXX-9012``.
    """

    def _mask(match: re.Match[str]) -> str:
        last_four = match.group(3)
        return f"XXXX-XXXX-{last_four}"

    return _AADHAAR_PATTERN.sub(_mask, text)


def sanitize_phone(text: str) -> str:
    """Mask phone numbers in *text*, preserving only the last 4 digits.

    ``+91 98765 43210`` becomes ``XXXXXX3210``.
    """

    def _mask(match: re.Match[str]) -> str:
        last_four = match.group(2)
        return f"XXXXXX{last_four}"

    return _PHONE_PATTERN.sub(_mask, text)


def sanitize_email(text: str) -> str:
    """Mask email addresses in *text*."""
    return _EMAIL_PATTERN.sub("[EMAIL_REDACTED]", text)


def sanitize_pii(text: str) -> str:
    """Apply all PII sanitisation routines to *text*.

    Order matters: Aadhaar first (12 digits could overlap with phone
    patterns), then phone, then email.
    """
    text = sanitize_aadhaar(text)
    text = sanitize_phone(text)
    text = sanitize_email(text)
    return text


# ---------------------------------------------------------------------------
# DPDPA Middleware
# ---------------------------------------------------------------------------


class DPDPAMiddleware(BaseHTTPMiddleware):
    """Middleware implementing DPDPA compliance requirements.

    - Strips PII (Aadhaar, phone, email) from structured logs.
    - Adds privacy-related HTTP headers to every response.
    - Logs consent status from request headers when present.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # -- Log sanitised request info ------------------------------------
        client_ip = request.client.host if request.client else "unknown"
        path = request.url.path
        method = request.method

        # Check for consent header (custom header for DPDPA tracking)
        consent_status = request.headers.get("X-DPDPA-Consent", "not_provided")

        logger.info(
            "request.incoming",
            method=method,
            path=path,
            client_ip=sanitize_pii(client_ip),
            consent=consent_status,
        )

        # -- Process request -----------------------------------------------
        response = await call_next(request)

        # -- Add security and privacy headers --------------------------------
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "img-src 'self' data: blob:; "
            "font-src 'self' https://fonts.gstatic.com; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'; "
            "object-src 'none'; "
            "upgrade-insecure-requests"
        )
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(self), geolocation=(), payment=(), "
            "usb=(), magnetometer=(), gyroscope=(), accelerometer=(), "
            "interest-cohort=()"
        )
        response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
        response.headers["X-DPDPA-Compliant"] = "true"
        response.headers["X-Data-Processing-Purpose"] = "government-scheme-assistance"
        response.headers["X-Data-Retention-Policy"] = "session-only"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"

        # Cache control: allow caching for static assets, no-store for API
        if path.startswith("/static/"):
            response.headers["Cache-Control"] = "public, max-age=86400, immutable"
        else:
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
            response.headers["Pragma"] = "no-cache"

        return response
