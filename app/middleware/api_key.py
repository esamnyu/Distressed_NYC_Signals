"""
API Key Authentication Middleware.

Provides Bearer token authentication with usage tracking and tier support.
"""

import logging
from typing import Optional

from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from ..config import get_settings
from .api_keys import get_api_key_manager, APIKeyData

logger = logging.getLogger(__name__)


class APIKeyMiddleware(BaseHTTPMiddleware):
    """
    API Key authentication middleware.

    Validates Bearer tokens, tracks usage, and enforces monthly limits.
    Stores key data in request.state for downstream access.
    """

    def __init__(self, app):
        super().__init__(app)
        self._settings = get_settings()

    def _extract_api_key(self, request: Request) -> Optional[str]:
        """Extract API key from Authorization header."""
        auth_header = request.headers.get(self._settings.api_key_header)

        if not auth_header:
            return None

        # Expect "Bearer sk_xxx" format
        parts = auth_header.split(" ", 1)

        if len(parts) != 2:
            return None

        scheme, token = parts

        if scheme.lower() != "bearer":
            return None

        return token

    async def dispatch(self, request: Request, call_next) -> Response:
        """Process request with API key validation and usage tracking."""
        # Skip auth for public endpoints
        path = request.url.path
        public_paths = ["/", "/health", "/ready", "/docs", "/openapi.json", "/redoc"]

        if path in public_paths:
            return await call_next(request)

        # Check if auth is required
        if not self._settings.require_api_key:
            # Auth not required - allow request but don't track
            return await call_next(request)

        # Extract API key
        api_key = self._extract_api_key(request)

        if not api_key:
            logger.warning(f"Missing API key for path: {path}")
            raise HTTPException(
                status_code=401,
                detail={
                    "error": True,
                    "code": "MISSING_API_KEY",
                    "message": "Missing API key. Use 'Authorization: Bearer sk_...' header.",
                },
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Validate key with manager
        manager = get_api_key_manager()
        key_data = manager.validate_key(api_key)

        if not key_data:
            logger.warning(f"Invalid API key attempt for path: {path}")
            raise HTTPException(
                status_code=403,
                detail={
                    "error": True,
                    "code": "INVALID_API_KEY",
                    "message": "Invalid or deactivated API key.",
                },
            )

        # Check monthly limit
        if key_data.is_over_monthly_limit:
            logger.warning(f"Monthly limit exceeded for key: {api_key[:12]}...")
            raise HTTPException(
                status_code=429,
                detail={
                    "error": True,
                    "code": "MONTHLY_LIMIT_EXCEEDED",
                    "message": f"Monthly request limit ({key_data.monthly_limit}) exceeded. Upgrade your plan or wait until next month.",
                    "limit": key_data.monthly_limit,
                    "used": key_data.calls_this_month,
                },
            )

        # Store key data in request state for downstream access
        request.state.api_key = api_key
        request.state.api_key_data = key_data
        request.state.user_id = key_data.user_id

        # Execute request
        response = await call_next(request)

        # Record usage after successful request
        if response.status_code < 400:
            manager.record_usage(api_key)

        # Add usage headers
        remaining = max(0, key_data.monthly_limit - key_data.calls_this_month - 1)
        response.headers["X-API-Key-User"] = key_data.user_id
        response.headers["X-API-Key-Tier"] = key_data.tier.value
        response.headers["X-Monthly-Limit"] = str(key_data.monthly_limit)
        response.headers["X-Monthly-Remaining"] = str(remaining)

        return response


def get_current_key_data(request: Request) -> Optional[APIKeyData]:
    """
    Get the API key data for the current request.

    Use this in route handlers to access key info:

        @router.get("/protected")
        async def protected_route(request: Request):
            key_data = get_current_key_data(request)
            if key_data:
                print(f"User: {key_data.user_id}, Tier: {key_data.tier}")
    """
    return getattr(request.state, "api_key_data", None)


def get_current_user_id(request: Request) -> Optional[str]:
    """Get the user ID for the current request."""
    return getattr(request.state, "user_id", None)
