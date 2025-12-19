# Routes module
from .v1 import router as v1_router
from .admin import router as admin_router

__all__ = ["v1_router", "admin_router"]
