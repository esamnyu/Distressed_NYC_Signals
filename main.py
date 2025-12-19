"""
NYC Distress Signal API - Main Application Entry Point.

A high-fidelity, real-time API that aggregates "Distress Signals" for NYC properties.
Queries municipal data sources (311 Complaints & DOB Violations) and computes
a normalized "Distress Score" (0-100).

Run with: uvicorn main:app --reload
"""

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

from app import __version__
from app.config import get_settings
from app.models import HealthResponse, ErrorResponse
from app.routes import v1_router, admin_router
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.api_key import APIKeyMiddleware
from app.middleware.error_handler import (
    ErrorHandlerMiddleware,
    create_validation_error_handler,
    create_http_exception_handler,
)
from app.middleware.request_logging import RequestLoggingMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.browser_manager import BrowserManager
from app.services.cache import get_cache_service

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# Global state
_browser_manager: BrowserManager = None
_settings = get_settings()
_start_time: float = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.

    Handles startup and shutdown events:
    - Startup: Initialize browser pool and cache
    - Shutdown: Clean up resources
    """
    global _browser_manager, _start_time

    # === STARTUP ===
    logger.info("Starting NYC Distress Signal API...")
    _start_time = time.time()

    # Initialize cache
    cache_service = get_cache_service()
    cache_service.initialize()
    logger.info("Cache service initialized")

    # Initialize browser pool
    try:
        _browser_manager = await BrowserManager.get_instance()
        logger.info("Browser manager initialized")
    except Exception as e:
        logger.warning(f"Failed to initialize browser: {e}")
        logger.warning("DOB scraping will be unavailable")

    logger.info(f"API v{__version__} ready")

    yield  # Application runs here

    # === SHUTDOWN ===
    logger.info("Shutting down...")

    # Close browser
    if _browser_manager:
        await _browser_manager.close()
        logger.info("Browser manager closed")

    # Close cache
    cache_service.close()
    logger.info("Cache service closed")

    logger.info("Shutdown complete")


# Create FastAPI app
app = FastAPI(
    title="NYC Distress Signal API",
    description="""
## Real-Time Property Distress Intelligence for NYC

This API aggregates municipal data to compute a **Distress Score** (0-100) for NYC properties.

### Data Sources
- **NYC 311 Complaints**: Illegal conversions, heat/water issues, noise complaints
- **NYC Department of Buildings**: Open violations, Stop Work Orders, Vacate Orders

### Use Cases
- Real Estate AI Agents
- PropTech Applications
- Wholesale Deal Sourcing
- Due Diligence Automation

### Scoring Algorithm
The Distress Score is computed based on:
- **+50 pts**: Active Vacate Order (Critical)
- **+30 pts**: Active Stop Work Order (Financial Distress)
- **+15 pts**: Multiple Illegal Conversion complaints
- **+5 pts**: Each Heat/Hot Water complaint
- **+3 pts**: Each open DOB violation
- **Max Score**: 100

### MCP Compliance
The `/v1/agent` endpoint is optimized for AI agents and LLMs with minimal token usage.
    """,
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add custom middleware (order matters - first added = outermost)
app.add_middleware(SecurityHeadersMiddleware)  # Security headers on all responses
app.add_middleware(RequestLoggingMiddleware)  # Log all requests
app.add_middleware(RateLimitMiddleware)
app.add_middleware(APIKeyMiddleware)
app.add_middleware(ErrorHandlerMiddleware)

# Add exception handlers for consistent error format
app.add_exception_handler(RequestValidationError, create_validation_error_handler())
app.add_exception_handler(HTTPException, create_http_exception_handler())

# Include routers
app.include_router(v1_router)
app.include_router(admin_router)


@app.get(
    "/",
    tags=["root"],
    summary="API Root",
    description="Welcome message and API information.",
)
async def root():
    """API root endpoint."""
    return {
        "name": "NYC Distress Signal API",
        "version": __version__,
        "docs": "/docs",
        "endpoints": {
            "analyze": "POST /v1/analyze",
            "agent": "POST /v1/agent",
            "health": "GET /health",
        },
    }


@app.get(
    "/health",
    tags=["health"],
    summary="Health Check",
    description="Detailed health check with component status.",
)
async def health_check():
    """
    Health check endpoint with detailed component status.

    Returns:
    - healthy: All systems operational
    - degraded: Some non-critical systems unavailable
    - unhealthy: Critical systems unavailable
    """
    global _browser_manager, _start_time

    cache_service = get_cache_service()

    # Check each component
    checks = {
        "cache": "ok" if cache_service.is_ready else "unavailable",
        "browser": "ok" if (_browser_manager and _browser_manager.is_ready) else "unavailable",
    }

    # Determine overall status
    critical_ok = checks["cache"] == "ok"  # Cache is critical
    all_ok = all(v == "ok" for v in checks.values())

    if all_ok:
        status = "healthy"
    elif critical_ok:
        status = "degraded"
    else:
        status = "unhealthy"

    # Calculate uptime
    uptime_seconds = int(time.time() - _start_time) if _start_time else 0

    return {
        "status": status,
        "version": __version__,
        "checks": checks,
        "uptime_seconds": uptime_seconds,
    }


@app.get(
    "/ready",
    tags=["health"],
    summary="Readiness Check",
    description="Kubernetes-style readiness probe.",
)
async def readiness_check():
    """
    Readiness check for load balancers and orchestrators.

    Returns 200 if ready to accept traffic, 503 otherwise.
    """
    global _browser_manager

    cache_service = get_cache_service()

    # Must have cache to be ready
    if not cache_service.is_ready:
        return JSONResponse(
            status_code=503,
            content={"ready": False, "reason": "Cache not available"},
        )

    return {"ready": True}


@app.get(
    "/cache/stats",
    tags=["admin"],
    summary="Cache Statistics",
    description="Get current cache statistics.",
)
async def cache_stats():
    """Get cache statistics."""
    cache_service = get_cache_service()
    return cache_service.stats()




if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=_settings.host,
        port=_settings.port,
        reload=_settings.debug,
    )
