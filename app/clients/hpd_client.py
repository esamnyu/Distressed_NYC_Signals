"""
NYC HPD Violations Client.

Fetches Housing Preservation & Development violation data via NYC OpenData.
HPD violations are critical distress signals for residential properties.
"""

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import List, Optional

from sodapy import Socrata

from ..config import get_settings

logger = logging.getLogger(__name__)

# NYC OpenData domain
NYC_OPENDATA_DOMAIN = "data.cityofnewyork.us"

# HPD Violations dataset ID
HPD_VIOLATIONS_DATASET = "wvxf-dwi5"


class HPDViolation:
    """Represents an HPD violation."""

    def __init__(
        self,
        violation_id: str,
        violation_class: str,  # A, B, or C
        status: str,
        inspection_date: Optional[str],
        nov_description: Optional[str],
        current_status_date: Optional[str],
    ):
        self.violation_id = violation_id
        self.violation_class = violation_class
        self.status = status
        self.inspection_date = inspection_date
        self.nov_description = nov_description
        self.current_status_date = current_status_date

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "violation_id": self.violation_id,
            "violation_class": self.violation_class,
            "status": self.status,
            "inspection_date": self.inspection_date,
            "description": self.nov_description,
            "status_date": self.current_status_date,
        }


class HPDData:
    """Aggregated HPD violation data."""

    def __init__(
        self,
        total_violations: int = 0,
        class_a_count: int = 0,
        class_b_count: int = 0,
        class_c_count: int = 0,
        open_violations: int = 0,
        violations: Optional[List[HPDViolation]] = None,
        fetched_at: Optional[datetime] = None,
        error: Optional[str] = None,
    ):
        self.total_violations = total_violations
        self.class_a_count = class_a_count
        self.class_b_count = class_b_count
        self.class_c_count = class_c_count
        self.open_violations = open_violations
        self.violations = violations or []
        self.fetched_at = fetched_at or datetime.now(timezone.utc)
        self.error = error

    def to_dict(self) -> dict:
        """Convert to dictionary."""
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
    """Client for fetching HPD violation data via NYC OpenData."""

    def __init__(self):
        self._settings = get_settings()
        self._client: Optional[Socrata] = None

    def _get_client(self) -> Socrata:
        """Get or create Socrata client."""
        if self._client is None:
            self._client = Socrata(
                NYC_OPENDATA_DOMAIN,
                self._settings.nyc_opendata_app_token,
                timeout=30,
            )
        return self._client

    def _sanitize_soql_value(self, value: str) -> str:
        """
        Sanitize a value for use in SoQL queries to prevent injection.
        """
        if not value:
            return ""

        value = value[:200]
        value = value.replace("'", "''")
        value = re.sub(r'[;\-\-\|\&\$\(\)\[\]\{\}]', '', value)
        return value.strip()

    async def fetch_violations_by_bbl(self, bbl: str) -> HPDData:
        """
        Fetch HPD violations by BBL.

        Args:
            bbl: Borough-Block-Lot identifier (10 digits)

        Returns:
            HPDData with violation counts and details
        """
        try:
            # Parse BBL components
            if len(bbl) != 10:
                return HPDData(error=f"Invalid BBL format: {bbl}")

            borough_id = bbl[0]
            block = bbl[1:6].lstrip("0") or "0"
            lot = bbl[6:10].lstrip("0") or "0"

            # Build query - HPD uses separate boroid, block, lot fields
            where_clause = (
                f"boroid = '{borough_id}' AND "
                f"block = '{block}' AND "
                f"lot = '{lot}'"
            )

            logger.info(f"Fetching HPD violations for BBL {bbl}: {where_clause}")

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

            logger.info(f"Found {len(results)} HPD violations")

            # Parse and categorize violations
            violations: List[HPDViolation] = []
            class_a_count = 0
            class_b_count = 0
            class_c_count = 0
            open_count = 0

            for v in results:
                violation_class = v.get("class", "").upper()
                status = v.get("currentstatus", "").upper()

                violation = HPDViolation(
                    violation_id=v.get("violationid", ""),
                    violation_class=violation_class,
                    status=status,
                    inspection_date=v.get("inspectiondate", "")[:10] if v.get("inspectiondate") else None,
                    nov_description=v.get("novdescription", ""),
                    current_status_date=v.get("currentstatusdate", "")[:10] if v.get("currentstatusdate") else None,
                )
                violations.append(violation)

                # Count by class
                if violation_class == "A":
                    class_a_count += 1
                elif violation_class == "B":
                    class_b_count += 1
                elif violation_class == "C":
                    class_c_count += 1

                # Count open violations
                if "OPEN" in status or status == "":
                    open_count += 1

            return HPDData(
                total_violations=len(results),
                class_a_count=class_a_count,
                class_b_count=class_b_count,
                class_c_count=class_c_count,
                open_violations=open_count,
                violations=violations[:50],  # Limit to 50 for response size
                fetched_at=datetime.now(timezone.utc),
            )

        except Exception as e:
            logger.error(f"Error fetching HPD data: {e}")
            return HPDData(error=str(e))

    async def fetch_violations_by_address(
        self,
        house_number: str,
        street: str,
        borough_id: str,
    ) -> HPDData:
        """
        Fetch HPD violations by address (fallback if BBL unavailable).

        Args:
            house_number: Property house number
            street: Street name
            borough_id: Borough ID (1-5)

        Returns:
            HPDData with violation counts and details
        """
        try:
            # Sanitize inputs to prevent SoQL injection
            house_num = self._sanitize_soql_value(house_number.upper())
            street_name = self._sanitize_soql_value(street.upper())
            # Borough ID should be 1-5, validate it
            if borough_id not in ('1', '2', '3', '4', '5'):
                return HPDData(error="Invalid borough ID")

            where_clause = (
                f"boroid = '{borough_id}' AND "
                f"UPPER(housenumber) = '{house_num}' AND "
                f"UPPER(streetname) LIKE '%{street_name}%'"
            )

            logger.info(f"Fetching HPD violations by address: {where_clause}")

            loop = asyncio.get_event_loop()
            results = await loop.run_in_executor(
                None,
                lambda: self._get_client().get(
                    HPD_VIOLATIONS_DATASET,
                    where=where_clause,
                    limit=1000,
                ),
            )

            # Same processing as BBL method
            violations: List[HPDViolation] = []
            class_a_count = 0
            class_b_count = 0
            class_c_count = 0
            open_count = 0

            for v in results:
                violation_class = v.get("class", "").upper()
                status = v.get("currentstatus", "").upper()

                violation = HPDViolation(
                    violation_id=v.get("violationid", ""),
                    violation_class=violation_class,
                    status=status,
                    inspection_date=v.get("inspectiondate", "")[:10] if v.get("inspectiondate") else None,
                    nov_description=v.get("novdescription", ""),
                    current_status_date=v.get("currentstatusdate", "")[:10] if v.get("currentstatusdate") else None,
                )
                violations.append(violation)

                if violation_class == "A":
                    class_a_count += 1
                elif violation_class == "B":
                    class_b_count += 1
                elif violation_class == "C":
                    class_c_count += 1

                if "OPEN" in status or status == "":
                    open_count += 1

            return HPDData(
                total_violations=len(results),
                class_a_count=class_a_count,
                class_b_count=class_b_count,
                class_c_count=class_c_count,
                open_violations=open_count,
                violations=violations[:50],
                fetched_at=datetime.now(timezone.utc),
            )

        except Exception as e:
            logger.error(f"Error fetching HPD data by address: {e}")
            return HPDData(error=str(e))

    def close(self) -> None:
        """Close the Socrata client."""
        if self._client:
            self._client.close()
            self._client = None


# Singleton instance
_client_instance: Optional[HPDClient] = None


def get_hpd_client() -> HPDClient:
    """Get the singleton HPD client instance."""
    global _client_instance
    if _client_instance is None:
        _client_instance = HPDClient()
    return _client_instance
