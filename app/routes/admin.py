"""
Admin API Routes.

Endpoints for API key management and administration.
Protected by master API key.
"""

import hashlib
import logging
import secrets
from typing import Optional

from fastapi import APIRouter, HTTPException, Header, status, Query
from pydantic import BaseModel, Field

from ..config import get_settings
from ..middleware.api_keys import (
    get_api_key_manager,
    APIKeyTier,
    TIER_MONTHLY_LIMITS,
    TIER_RATE_LIMITS,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


def verify_master_key(x_master_key: Optional[str] = Header(None)) -> None:
    """Verify the master API key for admin endpoints using constant-time comparison."""
    settings = get_settings()

    if not settings.admin_master_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin endpoints not configured.",
        )

    if not x_master_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )

    # Use constant-time comparison to prevent timing attacks
    if not secrets.compare_digest(x_master_key.encode(), settings.admin_master_key.encode()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed.",
        )


class CreateKeyRequest(BaseModel):
    """Request to create a new API key."""
    user_id: str = Field(..., min_length=1, max_length=100, description="Unique user identifier")
    tier: APIKeyTier = Field(default=APIKeyTier.FREE, description="Subscription tier")
    custom_monthly_limit: Optional[int] = Field(default=None, ge=1, description="Override default monthly limit")


class CreateKeyResponse(BaseModel):
    """Response after creating an API key."""
    api_key: str = Field(..., description="The generated API key (save this, it won't be shown again)")
    user_id: str
    tier: str
    monthly_limit: int
    rate_limit_per_minute: int


class KeyUsageResponse(BaseModel):
    """API key usage statistics."""
    user_id: str
    tier: str
    calls_used: int
    calls_this_month: int
    monthly_limit: int
    remaining_this_month: int
    rate_limit_per_minute: int
    created_at: str
    last_used_at: Optional[str]


@router.post(
    "/keys",
    response_model=CreateKeyResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create API Key",
    description="Create a new API key for a user. Requires master key.",
)
async def create_api_key(
    request: CreateKeyRequest,
    x_master_key: Optional[str] = Header(None),
):
    """Create a new API key."""
    verify_master_key(x_master_key)

    manager = get_api_key_manager()

    api_key = manager.create_key(
        user_id=request.user_id,
        tier=request.tier,
        custom_monthly_limit=request.custom_monthly_limit,
    )

    monthly_limit = request.custom_monthly_limit or TIER_MONTHLY_LIMITS.get(request.tier, 100)
    rate_limit = TIER_RATE_LIMITS.get(request.tier, 10)

    logger.info(f"Created API key for user: {request.user_id}")

    return CreateKeyResponse(
        api_key=api_key,
        user_id=request.user_id,
        tier=request.tier.value,
        monthly_limit=monthly_limit,
        rate_limit_per_minute=rate_limit,
    )


@router.get(
    "/keys",
    summary="List API Keys",
    description="List all API keys. Requires master key.",
)
async def list_api_keys(
    user_id: Optional[str] = None,
    x_master_key: Optional[str] = Header(None),
):
    """List all API keys, optionally filtered by user."""
    verify_master_key(x_master_key)

    manager = get_api_key_manager()
    keys = manager.list_keys(user_id=user_id)

    return {"keys": keys, "total": len(keys)}


@router.post(
    "/keys/usage",
    response_model=KeyUsageResponse,
    summary="Get Key Usage",
    description="Get usage statistics for an API key. Requires master key.",
)
async def get_key_usage(
    api_key: str = Query(..., description="The API key to look up", min_length=10),
    x_master_key: Optional[str] = Header(None),
):
    """Get usage statistics for an API key (key passed in body/query, not URL)."""
    verify_master_key(x_master_key)

    manager = get_api_key_manager()
    usage = manager.get_usage(api_key)

    if not usage:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Key not found.",
        )

    return KeyUsageResponse(**usage)


@router.post(
    "/keys/deactivate",
    summary="Deactivate Key",
    description="Deactivate an API key. Requires master key.",
)
async def deactivate_key(
    api_key: str = Query(..., description="The API key to deactivate", min_length=10),
    x_master_key: Optional[str] = Header(None),
):
    """Deactivate an API key (key passed in query, not URL path)."""
    verify_master_key(x_master_key)

    manager = get_api_key_manager()
    success = manager.deactivate_key(api_key)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Key not found.",
        )

    # Only log hash of key, not the key itself
    key_hash = hashlib.sha256(api_key.encode()).hexdigest()[:12]
    logger.info(f"Deactivated API key hash: {key_hash}...")

    return {"message": "Key deactivated"}


@router.post(
    "/keys/upgrade",
    summary="Upgrade Key Tier",
    description="Upgrade an API key to a higher tier. Requires master key.",
)
async def upgrade_key(
    api_key: str = Query(..., description="The API key to upgrade", min_length=10),
    tier: APIKeyTier = Query(..., description="New tier"),
    x_master_key: Optional[str] = Header(None),
):
    """Upgrade an API key to a new tier (key passed in query, not URL path)."""
    verify_master_key(x_master_key)

    manager = get_api_key_manager()
    success = manager.upgrade_tier(api_key, tier)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Key not found.",
        )

    logger.info(f"Upgraded key to tier: {tier.value}")

    return {"message": f"Upgraded to {tier.value}"}


@router.get(
    "/tiers",
    summary="List Tiers",
    description="List available subscription tiers. Requires master key.",
)
async def list_tiers(x_master_key: Optional[str] = Header(None)):
    """List available subscription tiers (protected)."""
    verify_master_key(x_master_key)

    tiers = []
    for tier in APIKeyTier:
        tiers.append({
            "tier": tier.value,
            "monthly_limit": TIER_MONTHLY_LIMITS.get(tier, 100),
            "rate_limit_per_minute": TIER_RATE_LIMITS.get(tier, 10),
        })

    return {"tiers": tiers}
