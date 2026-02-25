"""Simple in-memory rate limiter using a sliding window counter.

Uses a per-IP dictionary of :class:`collections.deque` timestamps for
O(1) amortised operations.  Designed for single-process deployments
(Cloud Run instances, dev servers); for multi-instance production use,
replace with a Redis-backed limiter.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Final

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

# Paths exempt from rate limiting (health checks, metrics).
_EXEMPT_PATHS: Final[frozenset[str]] = frozenset({
    "/api/v1/health",
    "/api/v1/health/ready",
    "/metrics",
    "/",
    "/docs",
    "/redoc",
    "/openapi.json",
})


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window rate limiter keyed by client IP address.

    Parameters
    ----------
    app:
        The ASGI application.
    max_requests_per_minute:
        Maximum number of requests allowed per IP per 60-second window.
    """

    def __init__(self, app: object, max_requests_per_minute: int = 60) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._max_rpm = max_requests_per_minute
        self._window_seconds: float = 60.0
        # IP -> deque of request timestamps (monotonic)
        self._requests: dict[str, deque[float]] = {}
        # Periodic cleanup counter to avoid unbounded memory growth
        self._cleanup_counter: int = 0
        self._cleanup_interval: int = 1000  # every N requests

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Skip rate limiting for exempt paths
        path = request.url.path
        if path in _EXEMPT_PATHS:
            return await call_next(request)

        client_ip = self._get_client_ip(request)
        now = time.monotonic()

        # Periodic cleanup of stale IP entries
        self._cleanup_counter += 1
        if self._cleanup_counter >= self._cleanup_interval:
            self._cleanup_counter = 0
            self._cleanup_stale_entries(now)

        # Get or create the deque for this IP
        if client_ip not in self._requests:
            self._requests[client_ip] = deque()

        window = self._requests[client_ip]

        # Remove timestamps outside the sliding window
        window_start = now - self._window_seconds
        while window and window[0] < window_start:
            window.popleft()

        # Check if over limit
        if len(window) >= self._max_rpm:
            # Calculate retry-after based on oldest request in window
            retry_after = int(self._window_seconds - (now - window[0])) + 1
            retry_after = max(1, retry_after)

            logger.warning(
                "rate_limit.exceeded",
                client_ip=client_ip,
                requests_in_window=len(window),
                max_rpm=self._max_rpm,
            )

            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Rate limit exceeded. Please try again later.",
                    "retry_after_seconds": retry_after,
                },
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(self._max_rpm),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(retry_after),
                },
            )

        # Record this request
        window.append(now)
        remaining = self._max_rpm - len(window)

        # Process request
        response = await call_next(request)

        # Add rate limit headers to response
        response.headers["X-RateLimit-Limit"] = str(self._max_rpm)
        response.headers["X-RateLimit-Remaining"] = str(remaining)

        return response

    def _get_client_ip(self, request: Request) -> str:
        """Extract the client IP from trusted proxy headers.

        SECURITY: X-Forwarded-For can be spoofed by clients.  We use the
        rightmost-minus-N strategy: take the Nth IP from the right of the
        X-Forwarded-For chain, where N is the number of trusted proxies
        (default 1, e.g. Cloud Run adds one entry).  This way, the
        attacker-controllable leftmost entries are ignored.

        Reference: https://adam-p.ca/blog/2022/03/x-forwarded-for/
        """
        from config.settings import settings

        trusted_proxies = getattr(settings, "trusted_proxy_count", 1)

        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            ips = [ip.strip() for ip in forwarded_for.split(",") if ip.strip()]
            # Take the Nth IP from the right (rightmost = set by the closest proxy)
            if len(ips) >= trusted_proxies:
                return ips[-trusted_proxies]
            # If fewer entries than expected proxies, use the leftmost (best effort)
            return ips[0] if ips else "unknown"

        # Fall back to direct connection IP
        if request.client:
            return request.client.host

        return "unknown"

    def _cleanup_stale_entries(self, now: float) -> None:
        """Remove IP entries whose entire window has expired.

        This prevents unbounded memory growth from one-time visitors.
        """
        window_start = now - self._window_seconds
        stale_ips = [
            ip for ip, dq in self._requests.items()
            if not dq or dq[-1] < window_start
        ]
        for ip in stale_ips:
            del self._requests[ip]

        if stale_ips:
            logger.debug("rate_limit.cleanup", removed_ips=len(stale_ips))
