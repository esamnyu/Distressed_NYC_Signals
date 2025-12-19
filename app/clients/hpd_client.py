"""
NYC HPD Violations Client.

Fetches Housing Preservation & Development violation data via NYC OpenData.
HPD violations are critical distress signals for residential properties.

Violation Classes:
- Class A: Non-hazardous (minor issues)
- Class B: Hazardous (significant issues)
- Class C: Immediately hazardous (critical issues)
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any, Tuple

from sodapy import Socrata

from ..config import get_settings
from ..utils import sanitize_soql_value

logger = logging.getLogger(__name__)

# NYC OpenData domain
NYC_OPENDATA_DOMAIN = "data.cityofnewyork.us"

# HPD Violations dataset ID
HPD_VIOLATIONS_DATASET = "wvxf-dwi5"

# Valid borough IDs
VALID_BOROUGH_IDS = frozenset(('1', '2', '3', '4', '5'))


@dataclass
class HPDViolation:
    """
    Represents a single HPD violation record.

    Attributes:
        violation_id: Unique identifier for the violation
        violation_class: Severity class (A=non-hazardous, B=hazardous, C=immediately hazardous)
        status: Current status of the violation
        inspection_date: Date of inspection (YYYY-MM-DD format)
        nov_description: Notice of Violation description
        current_status_date: Date of current status (YYYY-MM-DD format)
    """
    violation_id: str
    violation_class: str
    status: str
    inspection_date: Optional[str] = None
    nov_description: Optional[str] = None
    current_status_date: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "violation_id": self.violation_id,
            "violation_class": self.violation_class,
            "status": self.status,
            "inspection_date": self.inspection_date,
            "description": self.nov_description,
            "status_date": self.current_status_date,
        }


@dataclass
class HPDData:
    """
    Aggregated HPD violation data for a property.

    Attributes:
        total_violations: Total number of violations found
        class_a_count: Count of non-hazardous violations
        class_b_count: Count of hazardous violations
        class_c_count: Count of immediately hazardous violations
        open_violations: Count of violations still open
        violations: List of individual violation records (limited to 50)
        fetched_at: Timestamp when data was fetched
        error: Error message if fetch failed
    """
    total_violations: int = 0
    class_a_count: int = 0
    class_b_count: int = 0
    class_c_count: int = 0
    open_violations: int = 0
    violations: List[HPDViolation] = field(default_factory=list)
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "total_violations": self.total_violations,
            "class_a_count": self.class_a_count,
            "class_b_count": self.class_b_count,
            "class_c_count": self.class_c_count,
            "open_violations": self.open_violations,
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
            "error": self.error,
        }


class HPDClient:
    """
    Client for fetching HPD violation data via NYC OpenData Socrata API.

    Provides methods to fetch violations by BBL (preferred) or by address (fallback).
    Results are categorized by violation class and open/closed status.
    """

    def __init__(self):
        self._settings = get_settings()
        self._client: Optional[Socrata] = None

    def _get_client(self) -> Socrata:
        """Get or create Socrata client with configured timeout."""
        if self._client is None:
            self._client = Socrata(
                NYC_OPENDATA_DOMAIN,
                self._settings.nyc_opendata_app_token,
                timeout=30,
            )
        return self._client

    def _parse_violations(
        self, results: List[Dict[str, Any]]
    ) -> Tuple[List[HPDViolation], int, int, int, int]:
        """
        Parse raw Socrata results into HPDViolation objects with counts.

        Args:
            results: Raw results from Socrata API

        Returns:
            Tuple of (violations_list, class_a_count, class_b_count, class_c_count, open_count)
        """
        violations: List[HPDViolation] = []
        class_a_count = 0
        class_b_count = 0
        class_c_count = 0
        open_count = 0

        for v in results:
            violation_class = v.get("class", "").upper()
            status = v.get("currentstatus", "").upper()

            # Parse dates safely (extract YYYY-MM-DD from ISO format)
            inspection_date = None
            if v.get("inspectiondate"):
                inspection_date = v["inspectiondate"][:10]

            status_date = None
            if v.get("currentstatusdate"):
                status_date = v["currentstatusdate"][:10]

            violation = HPDViolation(
                violation_id=v.get("violationid", ""),
                violation_class=violation_class,
                status=status,
                inspection_date=inspection_date,
                nov_description=v.get("novdescription", ""),
                current_status_date=status_date,
            )
            violations.append(violation)

            # Count by class
            if violation_class == "A":
                class_a_count += 1
            elif violation_class == "B":
                class_b_count += 1
            elif violation_class == "C":
                class_c_count += 1

            # Count open violations (empty status also considered open)
            if "OPEN" in status or status == "":
                open_count += 1

        return violations, class_a_count, class_b_count, class_c_count, open_count

    def _build_hpd_data(
        self,
        results: List[Dict[str, Any]],
        max_violations: int = 50,
    ) -> HPDData:
        """
        Build HPDData from raw Socrata results.

        Args:
            results: Raw results from Socrata API
            max_violations: Maximum violations to include in response (default: 50)

        Returns:
            HPDData with parsed violations and counts
        """
        violations, class_a, class_b, class_c, open_count = self._parse_violations(results)

        return HPDData(
            total_violations=len(results),
            class_a_count=class_a,
            class_b_count=class_b,
            class_c_count=class_c,
            open_violations=open_count,
            violations=violations[:max_violations],
            fetched_at=datetime.now(timezone.utc),
        )

    async def fetch_violations_by_bbl(self, bbl: str) -> HPDData:
        """
        Fetch HPD violations by BBL (Borough-Block-Lot).

        This is the preferred lookup method as BBL provides exact property matching.

        Args:
            bbl: 10-digit Borough-Block-Lot identifier (e.g., "1000420031")

        Returns:
            HPDData with violation counts and details
        """
        try:
            # Validate BBL format
            if len(bbl) != 10 or not bbl.isdigit():
                return HPDData(error=f"Invalid BBL format: {bbl}")

            # Parse BBL components
            borough_id = bbl[0]
            block = bbl[1:6].lstrip("0") or "0"
            lot = bbl[6:10].lstrip("0") or "0"

            # Validate borough ID
            if borough_id not in VALID_BOROUGH_IDS:
                return HPDData(error=f"Invalid borough ID in BBL: {borough_id}")

            # Build query - HPD uses separate boroid, block, lot fields
            where_clause = (
                f"boroid = '{borough_id}' AND "
                f"block = '{block}' AND "
                f"lot = '{lot}'"
            )

            logger.info(f"Fetching HPD violations for BBL {bbl}")

            # Run blocking Socrata call in thread pool
            loop = asyncio.get_event_loop()
            results = await loop.run_in_executor(
                None,
                lambda: self._get_client().get(
                    HPD_VIOLATIONS_DATASET,
                    where=where_clause,
                    limit=1000,
                ),
            )

            logger.info(f"Found {len(results)} HPD violations for BBL {bbl}")

            return self._build_hpd_data(results)

        except Exception as e:
            logger.error(f"Error fetching HPD data for BBL {bbl}: {e}")
            return HPDData(error=str(e))

    async def fetch_violations_by_address(
        self,
        house_number: str,
        street: str,
        borough_id: str,
    ) -> HPDData:
        """
        Fetch HPD violations by address (fallback when BBL unavailable).

        Uses LIKE matching on street name for flexibility with abbreviations.

        Args:
            house_number: Property house number (e.g., "123" or "42-15")
            street: Street name (e.g., "Broadway" or "W 42nd St")
            borough_id: NYC borough ID ("1"=Manhattan, "2"=Bronx, "3"=Brooklyn,
                       "4"=Queens, "5"=Staten Island)

        Returns:
            HPDData with violation counts and details
        """
        try:
            # Validate borough ID
            if borough_id not in VALID_BOROUGH_IDS:
                return HPDData(error=f"Invalid borough ID: {borough_id}")

            # Sanitize inputs to prevent SoQL injection
            house_num = sanitize_soql_value(house_number.upper())
            street_name = sanitize_soql_value(street.upper())

            where_clause = (
                f"boroid = '{borough_id}' AND "
                f"UPPER(housenumber) = '{house_num}' AND "
                f"UPPER(streetname) LIKE '%{street_name}%'"
            )

            logger.info(f"Fetching HPD violations for {house_number} {street}, borough {borough_id}")

            loop = asyncio.get_event_loop()
            results = await loop.run_in_executor(
                None,
                lambda: self._get_client().get(
                    HPD_VIOLATIONS_DATASET,
                    where=where_clause,
                    limit=1000,
                ),
            )

            logger.info(f"Found {len(results)} HPD violations by address")

            return self._build_hpd_data(results)

        except Exception as e:
            logger.error(f"Error fetching HPD data by address: {e}")
            return HPDData(error=str(e))

    def close(self) -> None:
        """Close the Socrata client and release resources."""
        if self._client:
            self._client.close()
            self._client = None


# Singleton instance
_client_instance: Optional[HPDClient] = None


def get_hpd_client() -> HPDClient:
    """
    Get the singleton HPD client instance.

    Returns:
        HPDClient: Shared client instance for HPD violation queries
    """
    global _client_instance
    if _client_instance is None:
        _client_instance = HPDClient()
    return _client_instance
