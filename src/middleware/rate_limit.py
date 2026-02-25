"""Simple in-memory rate limiter using a sliding window counter.

Uses a per-IP dictionary of :class:`collections.deque` timestamps for
O(1) amortised operations.  Designed for single-process deployments
(Cloud Run instances, dev servers); for multi-instance production use,
replace with a Redis-backed limiter.
"""

from __future__ import annotations

import asyncio
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
    trusted_proxy_count:
        Number of trusted reverse proxies between the client and the
        application (e.g. 1 for Cloud Run behind its load balancer).
        The client IP is extracted from ``X-Forwarded-For`` by counting
        *trusted_proxy_count + 1* entries from the **right** of the
        header.  Set to 0 to fall back to the direct connection IP.
    """

    def __init__(
        self,
        app: object,
        max_requests_per_minute: int = 60,
        trusted_proxy_count: int = 1,
    ) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._max_rpm = max_requests_per_minute
        self._trusted_proxy_count = trusted_proxy_count
        self._window_seconds: float = 60.0
        # IP -> deque of request timestamps (monotonic)
        self._requests: dict[str, deque[float]] = {}
        # Lock to protect _requests from concurrent async access
        self._lock = asyncio.Lock()
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

        async with self._lock:
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

        # Process request (outside the lock)
        response = await call_next(request)

        # Add rate limit headers to response
        response.headers["X-RateLimit-Limit"] = str(self._max_rpm)
        response.headers["X-RateLimit-Remaining"] = str(remaining)

        return response

    def _get_client_ip(self, request: Request) -> str:
        """Extract the real client IP from behind trusted proxies.

        With ``trusted_proxy_count = N``, the rightmost N entries in
        ``X-Forwarded-For`` are proxy addresses.  The client address
        is the entry immediately before them, i.e.
        ``ips[-(N + 1)]``.

        If the header has fewer entries than expected we fall back to
        ``X-Real-IP`` or the direct connection address.
        """
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for and self._trusted_proxy_count > 0:
            ips = [ip.strip() for ip in forwarded_for.split(",")]
            # The client IP is at index -(trusted_proxy_count + 1):
            # e.g. header "client, proxy" with count=1 → ips[-2] = client
            client_index = -(self._trusted_proxy_count + 1)
            if abs(client_index) <= len(ips):
                return ips[client_index]
            # Header shorter than expected — use leftmost as best guess
            return ips[0]

        if forwarded_for:
            # trusted_proxy_count == 0: no trusted proxies, use leftmost
            return forwarded_for.split(",")[0].strip()

        # Check for X-Real-IP header
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip.strip()

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
