"""
Caching Layer using diskcache.

Provides persistent caching for API responses to minimize external requests.
"""

import hashlib
import json
import logging
from typing import Optional, Any
from datetime import datetime

import diskcache

from ..config import get_settings
from ..models import AnalysisResponse

logger = logging.getLogger(__name__)


class CacheService:
    """
    Disk-based cache service for storing analysis results.

    Uses diskcache for persistent storage with TTL support.
    """

    def __init__(self):
        self._settings = get_settings()
        self._cache: Optional[diskcache.Cache] = None
        self._initialized = False

    def initialize(self) -> None:
        """Initialize the cache directory."""
        if self._initialized:
            return

        try:
            self._cache = diskcache.Cache(
                self._settings.cache_directory,
                size_limit=2**30,  # 1GB limit
            )
            self._initialized = True
            logger.info(f"Cache initialized at {self._settings.cache_directory}")
        except Exception as e:
            logger.error(f"Failed to initialize cache: {e}")
            # Continue without cache - it's not critical
            self._initialized = False

    def close(self) -> None:
        """Close the cache."""
        if self._cache:
            try:
                self._cache.close()
            except Exception as e:
                logger.warning(f"Error closing cache: {e}")
            self._cache = None
            self._initialized = False

    @property
    def is_ready(self) -> bool:
        """Check if cache is ready."""
        return self._initialized and self._cache is not None

    def _make_key(self, house_number: str, street: str, borough: str) -> str:
        """
        Generate a cache key from address components.

        Uses MD5 hash for consistent, fixed-length keys.
        """
        # Normalize inputs
        normalized = f"{house_number.upper().strip()}|{street.upper().strip()}|{borough.upper()}"

        # Hash for shorter, cleaner keys
        key_hash = hashlib.md5(normalized.encode()).hexdigest()

        return f"analysis:{key_hash}"

    def get(
        self,
        house_number: str,
        street: str,
        borough: str,
    ) -> Optional[AnalysisResponse]:
        """
        Get cached analysis result.

        Args:
            house_number: Property house number
            street: Street name
            borough: Borough name

        Returns:
            Cached AnalysisResponse or None if not found/expired
        """
        if not self.is_ready:
            return None

        key = self._make_key(house_number, street, borough)

        try:
            cached_data = self._cache.get(key)

            if cached_data is None:
                logger.debug(f"Cache miss for key: {key}")
                return None

            # Deserialize from JSON
            data = json.loads(cached_data)
            response = AnalysisResponse(**data)

            logger.info(f"Cache hit for key: {key}")
            return response

        except Exception as e:
            logger.warning(f"Error reading from cache: {e}")
            return None

    def set(
        self,
        house_number: str,
        street: str,
        borough: str,
        response: AnalysisResponse,
    ) -> bool:
        """
        Cache an analysis result.

        Args:
            house_number: Property house number
            street: Street name
            borough: Borough name
            response: Analysis response to cache

        Returns:
            True if successfully cached, False otherwise
        """
        if not self.is_ready:
            return False

        key = self._make_key(house_number, street, borough)

        try:
            # Serialize to JSON
            # Convert datetime to ISO format for JSON serialization
            data = response.model_dump()
            data["last_updated"] = data["last_updated"].isoformat()

            cached_data = json.dumps(data)

            # Store with TTL
            self._cache.set(
                key,
                cached_data,
                expire=self._settings.cache_ttl_seconds,
            )

            logger.info(f"Cached result for key: {key} (TTL: {self._settings.cache_ttl_seconds}s)")
            return True

        except Exception as e:
            logger.warning(f"Error writing to cache: {e}")
            return False

    def delete(
        self,
        house_number: str,
        street: str,
        borough: str,
    ) -> bool:
        """
        Delete a cached entry.

        Returns:
            True if deleted, False otherwise
        """
        if not self.is_ready:
            return False

        key = self._make_key(house_number, street, borough)

        try:
            deleted = self._cache.delete(key)
            if deleted:
                logger.info(f"Deleted cache entry: {key}")
            return deleted
        except Exception as e:
            logger.warning(f"Error deleting from cache: {e}")
            return False

    def clear(self) -> bool:
        """
        Clear all cached entries.

        Returns:
            True if successful, False otherwise
        """
        if not self.is_ready:
            return False

        try:
            self._cache.clear()
            logger.info("Cache cleared")
            return True
        except Exception as e:
            logger.warning(f"Error clearing cache: {e}")
            return False

    def stats(self) -> dict:
        """Get cache statistics."""
        if not self.is_ready:
            return {"status": "not initialized"}

        try:
            return {
                "status": "ready",
                "size": len(self._cache),
                "directory": self._settings.cache_directory,
                "ttl_seconds": self._settings.cache_ttl_seconds,
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}


# Singleton instance
_cache_instance: Optional[CacheService] = None


def get_cache_service() -> CacheService:
    """Get the singleton cache service instance."""
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = CacheService()
    return _cache_instance
