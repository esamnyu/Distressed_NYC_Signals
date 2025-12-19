"""
FastAPI Middleware Module.

Request/response middleware for cross-cutting concerns:
- RateLimitMiddleware: Tiered rate limiting per API key/IP
- APIKeyMiddleware: Bearer token authentication and validation
- SecurityHeadersMiddleware: Security headers (HSTS, CSP, etc.)
- ErrorHandlerMiddleware: Consistent error response formatting
- RequestLoggingMiddleware: Structured request/response logging

Middleware is applied in order defined in main.py (first added = outermost).
"""

from .rate_limit import RateLimitMiddleware
from .api_key import APIKeyMiddleware, get_current_key_data, get_current_user_id
from .api_keys import get_api_key_manager, APIKeyManager, APIKeyTier
from .error_handler import ErrorHandlerMiddleware, APIError
from .request_logging import RequestLoggingMiddleware, get_request_logs
from .security_headers import SecurityHeadersMiddleware

__all__ = [
    "RateLimitMiddleware",
    "APIKeyMiddleware",
    "SecurityHeadersMiddleware",
    "ErrorHandlerMiddleware",
    "RequestLoggingMiddleware",
    "APIError",
    "get_current_key_data",
    "get_current_user_id",
    "get_api_key_manager",
    "APIKeyManager",
    "APIKeyTier",
    "get_request_logs",
]
