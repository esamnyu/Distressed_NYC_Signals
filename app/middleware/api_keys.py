"""
API Key Management System.

Provides JSON-based API key storage with usage tracking and tier support.
Designed for easy upgrade to a database backend later.
"""

import json
import logging
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any
from enum import Enum
from threading import Lock

logger = logging.getLogger(__name__)


class APIKeyTier(str, Enum):
    """API Key subscription tiers."""
    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


# Rate limits per tier (requests per minute)
TIER_RATE_LIMITS = {
    APIKeyTier.FREE: 10,
    APIKeyTier.PRO: 60,
    APIKeyTier.ENTERPRISE: 300,
}

# Monthly request limits per tier
TIER_MONTHLY_LIMITS = {
    APIKeyTier.FREE: 100,
    APIKeyTier.PRO: 5000,
    APIKeyTier.ENTERPRISE: 100000,
}


class APIKeyData:
    """Data structure for an API key."""

    def __init__(
        self,
        key: str,
        user_id: str,
        tier: APIKeyTier = APIKeyTier.FREE,
        monthly_limit: int = 100,
        calls_used: int = 0,
        calls_this_month: int = 0,
        current_month: str = None,
        created_at: str = None,
        last_used_at: str = None,
        is_active: bool = True,
    ):
        self.key = key
        self.user_id = user_id
        self.tier = tier if isinstance(tier, APIKeyTier) else APIKeyTier(tier)
        self.monthly_limit = monthly_limit
        self.calls_used = calls_used  # Total calls ever
        self.calls_this_month = calls_this_month
        self.current_month = current_month or datetime.now(timezone.utc).strftime("%Y-%m")
        self.created_at = created_at or datetime.now(timezone.utc).isoformat()
        self.last_used_at = last_used_at
        self.is_active = is_active

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON storage."""
        return {
            "user_id": self.user_id,
            "tier": self.tier.value,
            "monthly_limit": self.monthly_limit,
            "calls_used": self.calls_used,
            "calls_this_month": self.calls_this_month,
            "current_month": self.current_month,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
            "is_active": self.is_active,
        }

    @classmethod
    def from_dict(cls, key: str, data: Dict[str, Any]) -> "APIKeyData":
        """Create from dictionary."""
        return cls(
            key=key,
            user_id=data.get("user_id", "unknown"),
            tier=data.get("tier", "free"),
            monthly_limit=data.get("monthly_limit", 100),
            calls_used=data.get("calls_used", 0),
            calls_this_month=data.get("calls_this_month", 0),
            current_month=data.get("current_month"),
            created_at=data.get("created_at"),
            last_used_at=data.get("last_used_at"),
            is_active=data.get("is_active", True),
        )

    @property
    def rate_limit_per_minute(self) -> int:
        """Get rate limit for this key's tier."""
        return TIER_RATE_LIMITS.get(self.tier, 10)

    @property
    def is_over_monthly_limit(self) -> bool:
        """Check if monthly limit exceeded."""
        return self.calls_this_month >= self.monthly_limit


class APIKeyManager:
    """
    Manages API keys with JSON file storage.

    Thread-safe with file locking for concurrent access.
    """

    def __init__(self, storage_path: str = ".api_keys.json"):
        self._storage_path = Path(storage_path)
        self._lock = Lock()
        self._keys: Dict[str, APIKeyData] = {}
        self._load()

    def _load(self) -> None:
        """Load keys from JSON file."""
        if not self._storage_path.exists():
            self._keys = {}
            self._save()
            return

        try:
            with open(self._storage_path, "r") as f:
                data = json.load(f)
                self._keys = {
                    key: APIKeyData.from_dict(key, key_data)
                    for key, key_data in data.items()
                }
            logger.info(f"Loaded {len(self._keys)} API keys")
        except Exception as e:
            logger.error(f"Error loading API keys: {e}")
            self._keys = {}

    def _save(self) -> None:
        """Save keys to JSON file."""
        try:
            data = {
                key: key_data.to_dict()
                for key, key_data in self._keys.items()
            }
            with open(self._storage_path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving API keys: {e}")

    def generate_key(self, prefix: str = "sk_live_") -> str:
        """Generate a new API key."""
        return f"{prefix}{secrets.token_hex(16)}"

    def create_key(
        self,
        user_id: str,
        tier: APIKeyTier = APIKeyTier.FREE,
        custom_monthly_limit: Optional[int] = None,
    ) -> str:
        """
        Create a new API key for a user.

        Args:
            user_id: Unique identifier for the user
            tier: Subscription tier
            custom_monthly_limit: Override default monthly limit

        Returns:
            The generated API key string
        """
        with self._lock:
            key = self.generate_key()

            monthly_limit = custom_monthly_limit or TIER_MONTHLY_LIMITS.get(tier, 100)

            self._keys[key] = APIKeyData(
                key=key,
                user_id=user_id,
                tier=tier,
                monthly_limit=monthly_limit,
            )

            self._save()
            logger.info(f"Created new API key for user: {user_id}, tier: {tier.value}")

            return key

    def validate_key(self, key: str) -> Optional[APIKeyData]:
        """
        Validate an API key.

        Args:
            key: The API key to validate

        Returns:
            APIKeyData if valid, None otherwise
        """
        with self._lock:
            key_data = self._keys.get(key)

            if not key_data:
                return None

            if not key_data.is_active:
                return None

            # Check/reset monthly counter
            current_month = datetime.now(timezone.utc).strftime("%Y-%m")
            if key_data.current_month != current_month:
                key_data.current_month = current_month
                key_data.calls_this_month = 0
                self._save()

            return key_data

    def record_usage(self, key: str) -> bool:
        """
        Record a usage event for an API key.

        Args:
            key: The API key that made the request

        Returns:
            True if recorded successfully, False if over limit
        """
        with self._lock:
            key_data = self._keys.get(key)

            if not key_data:
                return False

            # Check monthly limit
            if key_data.is_over_monthly_limit:
                logger.warning(f"API key over monthly limit: {key[:12]}...")
                return False

            # Update counters
            key_data.calls_used += 1
            key_data.calls_this_month += 1
            key_data.last_used_at = datetime.now(timezone.utc).isoformat()

            self._save()
            return True

    def get_usage(self, key: str) -> Optional[Dict[str, Any]]:
        """Get usage statistics for an API key."""
        key_data = self._keys.get(key)

        if not key_data:
            return None

        return {
            "user_id": key_data.user_id,
            "tier": key_data.tier.value,
            "calls_used": key_data.calls_used,
            "calls_this_month": key_data.calls_this_month,
            "monthly_limit": key_data.monthly_limit,
            "remaining_this_month": max(0, key_data.monthly_limit - key_data.calls_this_month),
            "rate_limit_per_minute": key_data.rate_limit_per_minute,
            "created_at": key_data.created_at,
            "last_used_at": key_data.last_used_at,
        }

    def deactivate_key(self, key: str) -> bool:
        """Deactivate an API key."""
        with self._lock:
            key_data = self._keys.get(key)

            if not key_data:
                return False

            key_data.is_active = False
            self._save()
            logger.info(f"Deactivated API key: {key[:12]}...")
            return True

    def list_keys(self, user_id: Optional[str] = None) -> list:
        """List all keys, optionally filtered by user."""
        keys = []
        for key, data in self._keys.items():
            if user_id and data.user_id != user_id:
                continue
            keys.append({
                "key_prefix": key[:12] + "...",
                "user_id": data.user_id,
                "tier": data.tier.value,
                "is_active": data.is_active,
                "calls_this_month": data.calls_this_month,
                "monthly_limit": data.monthly_limit,
            })
        return keys

    def upgrade_tier(self, key: str, new_tier: APIKeyTier) -> bool:
        """Upgrade an API key to a new tier."""
        with self._lock:
            key_data = self._keys.get(key)

            if not key_data:
                return False

            key_data.tier = new_tier
            key_data.monthly_limit = TIER_MONTHLY_LIMITS.get(new_tier, 100)
            self._save()
            logger.info(f"Upgraded API key to tier: {new_tier.value}")
            return True


# Singleton instance
_manager_instance: Optional[APIKeyManager] = None


def get_api_key_manager() -> APIKeyManager:
    """Get the singleton API key manager instance."""
    global _manager_instance
    if _manager_instance is None:
        _manager_instance = APIKeyManager()
    return _manager_instance
