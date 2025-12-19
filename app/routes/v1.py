"""
API v1 Routes.

Main endpoints for the NYC Distress Signal API.
"""

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status, Query

from collections import defaultdict
from typing import List

from ..models import (
    AddressRequest,
    AnalysisResponse,
    AgentResponse,
    DOBStatus,
    NYC311Data,
    ErrorResponse,
    TimelineResponse,
    TimelineEvent,
    MonthlySummary,
)
from ..clients.nyc_311_client import get_311_client
from ..clients.hpd_client import get_hpd_client
from ..scrapers.dob_scraper import get_dob_scraper
from ..services.scoring import get_scorer, HPDDataInput
from ..services.geocoder import get_geocoder
from ..services.cache import get_cache_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["v1"])


async def _perform_analysis(address: AddressRequest) -> AnalysisResponse:
    """
    Perform full property distress analysis.

    This is the core logic shared between /analyze and /agent endpoints.
    """
    cache_service = get_cache_service()

    # Check cache first
    cached_result = cache_service.get(
        address.house_number,
        address.street,
        address.borough.value,
    )

    if cached_result:
        logger.info(f"Returning cached result for: {address.formatted_address}")
        return cached_result

    # Fetch data from all sources concurrently
    logger.info(f"Analyzing property: {address.formatted_address}")

    client_311 = get_311_client()
    dob_scraper = get_dob_scraper()
    hpd_client = get_hpd_client()
    geocoder = get_geocoder()

    # First, get BBL for accurate HPD lookup
    geo_result = await geocoder.lookup(
        address.house_number,
        address.street,
        address.borough,
    )

    bbl = geo_result.bbl if geo_result.is_valid else None
    logger.info(f"Geocoded to BBL: {bbl}")

    # Run data fetches concurrently
    nyc_311_task = client_311.fetch_complaints(
        address.house_number,
        address.street,
        address.borough,
    )

    dob_task = dob_scraper.get_dob_status(
        address.house_number,
        address.street,
        address.borough,
    )

    # HPD lookup - use BBL if available, otherwise address
    if bbl:
        hpd_task = hpd_client.fetch_violations_by_bbl(bbl)
    else:
        hpd_task = hpd_client.fetch_violations_by_address(
            address.house_number,
            address.street,
            str(address.borough_code),
        )

    # Wait for all to complete
    nyc_311_data, dob_status, hpd_data = await asyncio.gather(
        nyc_311_task,
        dob_task,
        hpd_task,
        return_exceptions=True,
    )

    # Handle exceptions
    if isinstance(nyc_311_data, Exception):
        logger.error(f"311 API error: {nyc_311_data}")
        nyc_311_data = NYC311Data(error=str(nyc_311_data))

    if isinstance(dob_status, Exception):
        logger.error(f"DOB scraper error: {dob_status}")
        dob_status = DOBStatus(error=str(dob_status))

    # Convert HPD data to scoring input
    hpd_input = None
    if isinstance(hpd_data, Exception):
        logger.error(f"HPD API error: {hpd_data}")
        hpd_input = HPDDataInput(error=str(hpd_data))
    else:
        hpd_input = HPDDataInput(
            class_a_count=hpd_data.class_a_count,
            class_b_count=hpd_data.class_b_count,
            class_c_count=hpd_data.class_c_count,
            open_violations=hpd_data.open_violations,
            error=hpd_data.error,
        )

    # Compute score with all data sources
    scorer = get_scorer()
    result = scorer.analyze(address, dob_status, nyc_311_data, hpd_input, bbl)

    # Cache the result
    cache_service.set(
        address.house_number,
        address.street,
        address.borough.value,
        result,
    )

    return result


@router.post(
    "/analyze",
    response_model=AnalysisResponse,
    responses={
        200: {"description": "Successful analysis"},
        400: {"model": ErrorResponse, "description": "Invalid request"},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
    summary="Analyze Property Distress",
    description="""
    Analyze a NYC property for distress signals.

    This endpoint aggregates data from:
    - NYC 311 Complaints (last 90 days)
    - NYC Department of Buildings (violations, orders)

    Returns a computed Distress Score (0-100) and detailed breakdown.

    **Example Request:**
    ```json
    {
        "house_number": "42-15",
        "street": "Crescent Street",
        "borough": "Queens"
    }
    ```
    """,
)
async def analyze_property(address: AddressRequest) -> AnalysisResponse:
    """
    Analyze a NYC property for distress signals.

    Returns full analysis with distress score, signals, and summary.
    """
    try:
        result = await _perform_analysis(address)
        return result

    except Exception as e:
        logger.exception(f"Error analyzing property: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Analysis failed: {str(e)}",
        )


@router.post(
    "/agent",
    response_model=AgentResponse,
    responses={
        200: {"description": "Successful analysis (minified)"},
        400: {"model": ErrorResponse, "description": "Invalid request"},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
    summary="Analyze Property (Agent/LLM Optimized)",
    description="""
    Analyze a NYC property and return a minified response optimized for AI agents.

    This endpoint returns a condensed single-string response to minimize
    token usage when called by LLMs (Claude, GPT, etc.) via MCP or function calling.

    **Example Response:**
    ```json
    {
        "response": "Score: 85/100. Signals: Vacate Order (YES), 311 Complaints (3). Status: CRITICAL."
    }
    ```
    """,
)
async def analyze_property_agent(address: AddressRequest) -> AgentResponse:
    """
    Analyze a property and return minified response for LLM agents.

    Reduces token count for AI agent consumption.
    """
    try:
        full_result = await _perform_analysis(address)
        return AgentResponse.from_analysis(full_result)

    except Exception as e:
        logger.exception(f"Error analyzing property for agent: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Analysis failed: {str(e)}",
        )


@router.delete(
    "/cache",
    responses={
        200: {"description": "Cache entry deleted"},
        404: {"description": "Cache entry not found"},
    },
    summary="Clear Cache for Address",
    description="Delete cached analysis result for a specific address.",
)
async def clear_cache(address: AddressRequest) -> dict:
    """Clear cached result for a specific address."""
    cache_service = get_cache_service()

    deleted = cache_service.delete(
        address.house_number,
        address.street,
        address.borough.value,
    )

    if deleted:
        return {"message": "Cache entry deleted", "address": address.formatted_address}
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No cache entry found for this address",
        )


def _aggregate_monthly(events: List[TimelineEvent]) -> List[MonthlySummary]:
    """Aggregate events by month."""
    monthly: dict = defaultdict(lambda: {"complaints": 0, "violations": 0})

    for event in events:
        if event.date and len(event.date) >= 7:
            period = event.date[:7]  # YYYY-MM
            if event.source.value == "311":
                monthly[period]["complaints"] += 1
            else:
                monthly[period]["violations"] += 1

    # Sort by period descending
    sorted_periods = sorted(monthly.keys(), reverse=True)

    return [
        MonthlySummary(
            period=period,
            complaint_count=monthly[period]["complaints"],
            violation_count=monthly[period]["violations"],
            total_events=monthly[period]["complaints"] + monthly[period]["violations"],
        )
        for period in sorted_periods
    ]


@router.post(
    "/timeline",
    response_model=TimelineResponse,
    responses={
        200: {"description": "Full property timeline"},
        400: {"model": ErrorResponse, "description": "Invalid request"},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
    summary="Property Timeline",
    description="""
    Get historical timeline of complaints and violations for a NYC property.

    Returns:
    - **events**: 311 complaints and DOB violations sorted by date (max 500)
    - **monthly_summary**: Events aggregated by month
    - **total_events**: Total count of all events
    - **earliest_date/latest_date**: Date range of events
    """,
)
async def get_property_timeline(
    address: AddressRequest,
    limit: int = Query(default=500, ge=1, le=500, description="Max events to return"),
) -> TimelineResponse:
    """
    Get full historical timeline for a property.

    Fetches all 311 complaints and DOB violations without date limits.
    """
    logger.info(f"Fetching timeline for: {address.formatted_address}")

    client_311 = get_311_client()
    dob_scraper = get_dob_scraper()

    # Fetch both histories concurrently
    nyc_311_task = client_311.fetch_full_history(
        address.house_number,
        address.street,
        address.borough,
    )

    dob_task = dob_scraper.get_violation_history(
        address.house_number,
        address.street,
        address.borough,
    )

    results = await asyncio.gather(
        nyc_311_task,
        dob_task,
        return_exceptions=True,
    )

    # Combine events
    all_events: List[TimelineEvent] = []
    partial_data = False

    # Handle 311 results
    if isinstance(results[0], Exception):
        logger.error(f"311 history error: {results[0]}")
        partial_data = True
    else:
        all_events.extend(results[0])

    # Handle DOB results
    if isinstance(results[1], Exception):
        logger.error(f"DOB history error: {results[1]}")
        partial_data = True
    else:
        all_events.extend(results[1])

    # Sort all events by date descending
    all_events.sort(key=lambda e: e.date if e.date != "Unknown" else "0000-00-00", reverse=True)

    # Limit response size to prevent DoS
    total_before_limit = len(all_events)
    all_events = all_events[:limit]

    # Calculate date range
    valid_dates = [e.date for e in all_events if e.date and e.date != "Unknown"]
    earliest_date = min(valid_dates) if valid_dates else None
    latest_date = max(valid_dates) if valid_dates else None

    # Aggregate by month
    monthly_summary = _aggregate_monthly(all_events)

    return TimelineResponse(
        address=address.formatted_address,
        events=all_events,
        monthly_summary=monthly_summary,
        total_events=total_before_limit,  # Report actual total, not limited count
        earliest_date=earliest_date,
        latest_date=latest_date,
        partial_data=partial_data or (total_before_limit > limit),  # Mark if truncated
        fetched_at=datetime.now(timezone.utc),
    )
