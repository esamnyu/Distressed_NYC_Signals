"""
Rate Limiting Middleware.

Implements per-API-key rate limiting with tier-based limits.
Falls back to IP-based limiting for unauthenticated requests.
"""

import ipaddress
import time
import logging
from collections import defaultdict
from typing import Dict, Tuple, Optional

from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from ..config import get_settings
from .api_keys import TIER_RATE_LIMITS, APIKeyTier

logger = logging.getLogger(__name__)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    In-memory rate limiting middleware.

    Supports:
    - Per-API-key rate limits based on subscription tier
    - Per-IP fallback for unauthenticated requests
    - Sliding window algorithm
    """

    def __init__(self, app):
        super().__init__(app)
        self._settings = get_settings()

        # Track request timestamps per identifier (API key or IP)
        # Format: {identifier: [timestamp1, timestamp2, ...]}
        self._requests: Dict[str, list] = defaultdict(list)

        # Window size in seconds (60 seconds = 1 minute for tier-based limits)
        self._window_size = 60.0

    def _is_trusted_proxy(self, ip: str) -> bool:
        """Check if an IP is a trusted proxy."""
        if not self._settings.trusted_proxies:
            return False

        try:
            ip_addr = ipaddress.ip_address(ip)
            for proxy in self._settings.trusted_proxies:
                try:
                    if '/' in proxy:
                        # CIDR notation
                        if ip_addr in ipaddress.ip_network(proxy, strict=False):
                            return True
                    else:
                        if ip_addr == ipaddress.ip_address(proxy):
                            return True
                except ValueError:
                    continue
        except ValueError:
            return False

        return False

    def _get_client_ip(self, request: Request) -> str:
        """
        Extract client IP from request with proxy spoofing protection.

        Only trusts X-Forwarded-For if the direct connection is from a trusted proxy.
        """
        # Get direct connection IP
        direct_ip = request.client.host if request.client else "unknown"

        # Only trust proxy headers if connection is from trusted proxy
        if self._is_trusted_proxy(direct_ip):
            forwarded = request.headers.get("X-Forwarded-For")
            if forwarded:
                # Get the rightmost untrusted IP (client IP)
                ips = [ip.strip() for ip in forwarded.split(",")]
                for ip in reversed(ips):
                    if not self._is_trusted_proxy(ip):
                        return ip

            real_ip = request.headers.get("X-Real-IP")
            if real_ip:
                return real_ip

        # Return direct connection IP (don't trust headers from untrusted sources)
        return direct_ip

    def _get_rate_limit(self, request: Request) -> Tuple[str, int]:
        """
        Get rate limit identifier and limit for this request.

        Returns:
            Tuple of (identifier, requests_per_minute)
        """
        # Check if we have API key data from auth middleware
        api_key = getattr(request.state, "api_key", None)
        key_data = getattr(request.state, "api_key_data", None)

        if api_key and key_data:
            # Use API key with tier-based limit
            tier = key_data.tier if hasattr(key_data, "tier") else APIKeyTier.FREE
            limit = TIER_RATE_LIMITS.get(tier, 10)
            return f"key:{api_key}", limit

        # Fall back to IP-based limiting
        ip = self._get_client_ip(request)
        # Default limit for unauthenticated: 10 req/min
        return f"ip:{ip}", 10

    def _is_rate_limited(self, identifier: str, limit: int) -> Tuple[bool, float, int]:
        """
        Check if identifier is rate limited.

        Args:
            identifier: API key or IP identifier
            limit: Maximum requests per minute

        Returns:
            Tuple of (is_limited, retry_after_seconds, remaining_requests)
        """
        now = time.time()
        window_start = now - self._window_size

        # Clean old entries
        self._requests[identifier] = [
            ts for ts in self._requests[identifier]
            if ts > window_start
        ]

        # Check rate
        request_count = len(self._requests[identifier])
        remaining = max(0, limit - request_count)

        if request_count >= limit:
            # Calculate retry-after (time until oldest request expires)
            oldest = min(self._requests[identifier]) if self._requests[identifier] else now
            retry_after = self._window_size - (now - oldest)
            return True, max(1.0, retry_after), 0

        return False, 0.0, remaining

    def _record_request(self, identifier: str) -> None:
        """Record a request timestamp for identifier."""
        self._requests[identifier].append(time.time())

    async def dispatch(self, request: Request, call_next) -> Response:
        """Process request with rate limiting."""
        # Skip rate limiting for health/docs endpoints
        path = request.url.path
        if path in ["/", "/health", "/ready", "/docs", "/openapi.json", "/redoc"]:
            return await call_next(request)

        # Get rate limit identifier and limit
        identifier, limit = self._get_rate_limit(request)

        # Check rate limit
        is_limited, retry_after, remaining = self._is_rate_limited(identifier, limit)

        if is_limited:
            logger.warning(f"Rate limit exceeded for: {identifier[:20]}...")
            raise HTTPException(
                status_code=429,
                detail={
                    "error": True,
                    "code": "RATE_LIMIT_EXCEEDED",
                    "message": f"Rate limit exceeded ({limit} requests/minute). Retry after {int(retry_after)} seconds.",
                    "limit": limit,
                    "retry_after": int(retry_after),
                },
                headers={"Retry-After": str(int(retry_after))},
            )

        # Record this request
        self._record_request(identifier)

        # Continue with request
        response = await call_next(request)

        # Recalculate remaining after recording
        _, _, remaining = self._is_rate_limited(identifier, limit)

        # Add rate limit headers
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(int(time.time() + self._window_size))
        response.headers["X-RateLimit-Window"] = "60"  # Window size in seconds

        return response
