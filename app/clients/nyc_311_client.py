"""
NYC 311 API Client using Socrata Open Data API (sodapy).

Fetches complaint data for properties from the NYC 311 Service Requests dataset.
"""

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional

from sodapy import Socrata

from ..config import get_settings
from ..models import NYC311Data, Borough, TimelineEvent, EventSource

logger = logging.getLogger(__name__)

# NYC OpenData domain
NYC_OPENDATA_DOMAIN = "data.cityofnewyork.us"

# Complaint type keywords for categorization
ILLEGAL_CONVERSION_KEYWORDS = [
    "illegal conversion",
    "illegal alteration",
    "illegal use",
]

HEAT_WATER_KEYWORDS = [
    "heat/hot water",
    "heating",
    "hot water",
    "no heat",
    "no hot water",
]

NOISE_RESIDENTIAL_KEYWORDS = [
    "noise - residential",
    "noise residential",
    "loud music/party",
]


class NYC311Client:
    """Client for fetching NYC 311 complaint data via Socrata API."""

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

        - Removes or escapes dangerous characters
        - Limits length to prevent buffer issues
        """
        if not value:
            return ""

        # Limit length
        value = value[:200]

        # Remove/escape SoQL special characters that could enable injection
        # Single quotes are the main injection vector in SoQL
        value = value.replace("'", "''")  # Escape single quotes by doubling

        # Remove other potentially dangerous characters
        value = re.sub(r'[;\-\-\|\&\$\(\)\[\]\{\}]', '', value)

        return value.strip()

    def _get_borough_name(self, borough: Borough) -> str:
        """Convert Borough enum to 311 dataset format."""
        # 311 data uses uppercase borough names
        mapping = {
            Borough.MANHATTAN: "MANHATTAN",
            Borough.BRONX: "BRONX",
            Borough.BROOKLYN: "BROOKLYN",
            Borough.QUEENS: "QUEENS",
            Borough.STATEN_ISLAND: "STATEN ISLAND",
        }
        return mapping[borough]

    def _categorize_complaint(self, complaint_type: str) -> str:
        """Categorize a complaint type into our signal categories."""
        complaint_lower = complaint_type.lower()

        for keyword in ILLEGAL_CONVERSION_KEYWORDS:
            if keyword in complaint_lower:
                return "illegal_conversion"

        for keyword in HEAT_WATER_KEYWORDS:
            if keyword in complaint_lower:
                return "heat_water"

        for keyword in NOISE_RESIDENTIAL_KEYWORDS:
            if keyword in complaint_lower:
                return "noise_residential"

        return "other"

    async def fetch_complaints(
        self,
        house_number: str,
        street: str,
        borough: Borough,
    ) -> NYC311Data:
        """
        Fetch 311 complaints for a given address.

        Args:
            house_number: Property house number
            street: Street name
            borough: NYC borough

        Returns:
            NYC311Data object with categorized complaint counts
        """
        try:
            # Calculate lookback date
            lookback_date = datetime.now() - timedelta(
                days=self._settings.nyc_311_lookback_days
            )
            lookback_str = lookback_date.strftime("%Y-%m-%dT00:00:00.000")

            # Sanitize inputs to prevent SoQL injection
            house_num_safe = self._sanitize_soql_value(house_number.upper())
            street_safe = self._sanitize_soql_value(street.upper())
            borough_name = self._get_borough_name(borough)  # Enum-based, safe

            # Query with WHERE clause for address matching
            # Using LIKE for partial street matching
            where_clause = (
                f"incident_address LIKE '%{house_num_safe}%{street_safe}%' "
                f"AND borough = '{borough_name}' "
                f"AND created_date > '{lookback_str}'"
            )

            logger.info(f"Fetching 311 complaints with query: {where_clause}")

            # Run blocking Socrata call in thread pool
            loop = asyncio.get_event_loop()
            results = await loop.run_in_executor(
                None,
                lambda: self._get_client().get(
                    self._settings.nyc_311_dataset_id,
                    where=where_clause,
                    limit=500,  # Reasonable limit for a single property
                ),
            )

            logger.info(f"Found {len(results)} 311 complaints")

            # Categorize complaints
            illegal_conversion_count = 0
            heat_water_count = 0
            noise_residential_count = 0
            other_count = 0

            for complaint in results:
                complaint_type = complaint.get("complaint_type", "")
                category = self._categorize_complaint(complaint_type)

                if category == "illegal_conversion":
                    illegal_conversion_count += 1
                elif category == "heat_water":
                    heat_water_count += 1
                elif category == "noise_residential":
                    noise_residential_count += 1
                else:
                    other_count += 1

            return NYC311Data(
                total_complaints=len(results),
                illegal_conversion_count=illegal_conversion_count,
                heat_water_count=heat_water_count,
                noise_residential_count=noise_residential_count,
                other_complaints=other_count,
                fetched_at=datetime.now(timezone.utc),
            )

        except Exception as e:
            logger.error(f"Error fetching 311 data: {e}")
            return NYC311Data(
                error=str(e),
                fetched_at=datetime.now(timezone.utc),
            )

    async def fetch_full_history(
        self,
        house_number: str,
        street: str,
        borough: Borough,
    ) -> List[TimelineEvent]:
        """
        Fetch full complaint history for a property (no date limit).

        Args:
            house_number: Property house number
            street: Street name
            borough: NYC borough

        Returns:
            List of TimelineEvent objects sorted by date descending
        """
        try:
            # Sanitize inputs to prevent SoQL injection
            house_num_safe = self._sanitize_soql_value(house_number.upper())
            street_safe = self._sanitize_soql_value(street.upper())
            borough_name = self._get_borough_name(borough)  # Enum-based, safe

            # Query without date filter for full history
            where_clause = (
                f"incident_address LIKE '%{house_num_safe}%{street_safe}%' "
                f"AND borough = '{borough_name}'"
            )

            logger.info(f"Fetching full 311 history: {where_clause}")

            loop = asyncio.get_event_loop()
            results = await loop.run_in_executor(
                None,
                lambda: self._get_client().get(
                    self._settings.nyc_311_dataset_id,
                    where=where_clause,
                    order="created_date DESC",
                    limit=5000,  # Higher limit for full history
                ),
            )

            logger.info(f"Found {len(results)} total 311 complaints in history")

            events = []
            for complaint in results:
                created_date = complaint.get("created_date", "")
                if created_date:
                    date_str = created_date[:10]  # Extract YYYY-MM-DD
                else:
                    date_str = "Unknown"

                complaint_type = complaint.get("complaint_type", "Unknown")
                descriptor = complaint.get("descriptor", "")
                status = complaint.get("status", "")
                resolution = complaint.get("resolution_description", "")

                description = descriptor
                if resolution:
                    description = f"{descriptor} - {resolution[:100]}" if descriptor else resolution[:100]

                events.append(TimelineEvent(
                    date=date_str,
                    source=EventSource.NYC_311,
                    event_type=complaint_type,
                    description=description or None,
                    status=status or None,
                ))

            return events

        except Exception as e:
            logger.error(f"Error fetching 311 history: {e}")
            return []

    def close(self) -> None:
        """Close the Socrata client."""
        if self._client:
            self._client.close()
            self._client = None


# Singleton instance
_client_instance: Optional[NYC311Client] = None


def get_311_client() -> NYC311Client:
    """Get the singleton 311 client instance."""
    global _client_instance
    if _client_instance is None:
        _client_instance = NYC311Client()
    return _client_instance
