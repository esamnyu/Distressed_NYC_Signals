"""
API Routes Module.

Contains all FastAPI router definitions:
- v1_router: Main API endpoints (analyze, timeline, agent)
- admin_router: Administrative endpoints (API key management)
"""

from .v1 import router as v1_router
from .admin import router as admin_router

__all__ = ["v1_router", "admin_router"]
