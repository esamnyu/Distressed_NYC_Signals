"""
Global Error Handler Middleware.

Provides consistent error response formatting across all endpoints.
"""

import logging
import uuid
from typing import Optional

from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from ..config import get_settings

logger = logging.getLogger(__name__)


class APIError(Exception):
    """Custom API error with structured response."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        details: Optional[dict] = None,
    ):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        super().__init__(message)


def format_error_response(
    code: str,
    message: str,
    request_id: str,
    details: Optional[dict] = None,
) -> dict:
    """Format a consistent error response."""
    response = {
        "error": True,
        "code": code,
        "message": message,
        "request_id": request_id,
    }
    if details:
        response["details"] = details
    return response


class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    """
    Middleware that catches all exceptions and returns consistent error responses.

    Adds request ID to all responses for debugging/support.
    """

    def __init__(self, app):
        super().__init__(app)
        self._settings = get_settings()

    async def dispatch(self, request: Request, call_next) -> Response:
        """Process request and handle any errors consistently."""
        # Generate request ID
        request_id = f"req_{uuid.uuid4().hex[:12]}"
        request.state.request_id = request_id

        try:
            response = await call_next(request)
            # Add request ID to response headers
            response.headers["X-Request-ID"] = request_id
            return response

        except Exception as exc:
            # Log the error
            logger.exception(f"Unhandled exception [request_id={request_id}]: {exc}")

            # Determine error details
            if isinstance(exc, APIError):
                status_code = exc.status_code
                error_response = format_error_response(
                    code=exc.code,
                    message=exc.message,
                    request_id=request_id,
                    details=exc.details,
                )
            elif isinstance(exc, HTTPException):
                status_code = exc.status_code
                # Handle structured detail (dict) or string detail
                if isinstance(exc.detail, dict):
                    error_response = {
                        **exc.detail,
                        "request_id": request_id,
                    }
                else:
                    error_response = format_error_response(
                        code="HTTP_ERROR",
                        message=str(exc.detail),
                        request_id=request_id,
                    )
            else:
                status_code = 500
                # Only show details in debug mode
                message = str(exc) if self._settings.debug else "An internal error occurred"
                error_response = format_error_response(
                    code="INTERNAL_ERROR",
                    message=message,
                    request_id=request_id,
                )

            return JSONResponse(
                status_code=status_code,
                content=error_response,
                headers={"X-Request-ID": request_id},
            )


def create_validation_error_handler():
    """Create a handler for FastAPI validation errors."""

    async def validation_error_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        """Handle validation errors with consistent format."""
        request_id = getattr(request.state, "request_id", f"req_{uuid.uuid4().hex[:12]}")

        # Extract field errors
        errors = []
        for error in exc.errors():
            field = ".".join(str(loc) for loc in error.get("loc", []))
            errors.append({
                "field": field,
                "message": error.get("msg", "Invalid value"),
                "type": error.get("type", "value_error"),
            })

        error_response = format_error_response(
            code="VALIDATION_ERROR",
            message="Request validation failed",
            request_id=request_id,
            details={"errors": errors},
        )

        return JSONResponse(
            status_code=422,
            content=error_response,
            headers={"X-Request-ID": request_id},
        )

    return validation_error_handler


def create_http_exception_handler():
    """Create a handler for HTTP exceptions."""

    async def http_exception_handler(
        request: Request,
        exc: HTTPException,
    ) -> JSONResponse:
        """Handle HTTP exceptions with consistent format."""
        request_id = getattr(request.state, "request_id", f"req_{uuid.uuid4().hex[:12]}")

        # Handle structured detail (dict) or string detail
        if isinstance(exc.detail, dict):
            error_response = {
                **exc.detail,
                "request_id": request_id,
            }
        else:
            error_response = format_error_response(
                code=f"HTTP_{exc.status_code}",
                message=str(exc.detail),
                request_id=request_id,
            )

        return JSONResponse(
            status_code=exc.status_code,
            content=error_response,
            headers={"X-Request-ID": request_id, **(exc.headers or {})},
        )

    return http_exception_handler
