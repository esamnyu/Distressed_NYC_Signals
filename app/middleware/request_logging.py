"""
Request Logging Middleware.

Logs all API requests in JSON format for analytics and debugging.
"""

import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional
from pathlib import Path

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from ..config import get_settings

logger = logging.getLogger(__name__)

# Dedicated request logger
request_logger = logging.getLogger("api.requests")


def setup_request_logging(log_file: str = "logs/requests.jsonl") -> None:
    """
    Set up file logging for API requests.

    Logs in JSON Lines format for easy parsing.
    """
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Create file handler with rotation
    from logging.handlers import RotatingFileHandler

    handler = RotatingFileHandler(
        log_path,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
    )
    handler.setLevel(logging.INFO)

    # JSON format
    formatter = logging.Formatter("%(message)s")
    handler.setFormatter(formatter)

    request_logger.addHandler(handler)
    request_logger.setLevel(logging.INFO)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Middleware that logs all requests in structured JSON format.

    Captures:
    - Timestamp
    - Method and path
    - API key (masked)
    - Response status and time
    - User agent
    - Client IP
    """

    def __init__(self, app, log_to_file: bool = True):
        super().__init__(app)
        self._settings = get_settings()

        if log_to_file:
            setup_request_logging()

    def _mask_api_key(self, key: Optional[str]) -> Optional[str]:
        """Mask API key for logging (show first 8 chars only)."""
        if not key:
            return None
        if len(key) > 12:
            return key[:8] + "..."
        return key[:4] + "..."

    def _get_client_ip(self, request: Request) -> str:
        """Extract client IP from request."""
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()

        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip

        if request.client:
            return request.client.host

        return "unknown"

    async def dispatch(self, request: Request, call_next) -> Response:
        """Log request and response details."""
        start_time = time.time()

        # Get request details
        method = request.method
        path = request.url.path
        query = str(request.url.query) if request.url.query else None
        client_ip = self._get_client_ip(request)
        user_agent = request.headers.get("User-Agent", "unknown")

        # Get API key (will be set by auth middleware)
        api_key = None
        user_id = None

        # Execute request
        response = await call_next(request)

        # Calculate duration
        duration_ms = (time.time() - start_time) * 1000

        # Get API key from request state (set by auth middleware)
        api_key = getattr(request.state, "api_key", None)
        user_id = getattr(request.state, "user_id", None)
        request_id = getattr(request.state, "request_id", None)

        # Build log entry
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "request_id": request_id,
            "method": method,
            "path": path,
            "query": query,
            "status_code": response.status_code,
            "duration_ms": round(duration_ms, 2),
            "client_ip": client_ip,
            "user_id": user_id,
            "api_key": self._mask_api_key(api_key),
            "user_agent": user_agent[:100] if user_agent else None,  # Truncate long UAs
        }

        # Log to file (JSON)
        request_logger.info(json.dumps(log_entry))

        # Also log summary to standard logger
        logger.info(
            f"{method} {path} - {response.status_code} - {duration_ms:.1f}ms - "
            f"user={user_id or 'anonymous'}"
        )

        return response


def get_request_logs(
    limit: int = 100,
    offset: int = 0,
    log_file: str = "logs/requests.jsonl",
) -> list:
    """
    Read recent request logs.

    Returns parsed JSON log entries.
    """
    log_path = Path(log_file)

    if not log_path.exists():
        return []

    logs = []
    with open(log_path, "r") as f:
        lines = f.readlines()

    # Get most recent entries (reverse order)
    for line in reversed(lines[-(offset + limit):]):
        if offset > 0:
            offset -= 1
            continue
        try:
            logs.append(json.loads(line.strip()))
        except json.JSONDecodeError:
            continue
        if len(logs) >= limit:
            break

    return logs
