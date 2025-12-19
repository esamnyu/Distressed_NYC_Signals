"""
Business Logic Services Module.

Core services for the NYC Distress Signal API:
- DistressScorer: Calculates distress scores (0-100) from property signals
- CacheService: Disk-based caching with TTL for analysis results
- NYCGeocoder: Address to BBL (Borough-Block-Lot) conversion

These services contain the primary business logic and are used by routes.
"""

from .scoring import DistressScorer, get_scorer
from .cache import CacheService, get_cache_service
from .geocoder import NYCGeocoder, get_geocoder

__all__ = [
    "DistressScorer",
    "get_scorer",
    "CacheService",
    "get_cache_service",
    "NYCGeocoder",
    "get_geocoder",
]
