"""
NYC Geocoder Service for BBL Lookup.

Uses NYC Planning Labs GeoSearch API to convert addresses to BBL
(Borough-Block-Lot) identifiers for exact property matching.
"""

import asyncio
import logging
from typing import Optional, Dict, Any

import httpx

from ..models import Borough

logger = logging.getLogger(__name__)

# NYC Planning Labs GeoSearch API (free, no key required)
GEOSEARCH_BASE_URL = "https://geosearch.planninglabs.nyc/v2/search"


class GeocoderResult:
    """Result from geocoder lookup."""

    def __init__(
        self,
        bbl: Optional[str] = None,
        bin_number: Optional[str] = None,
        normalized_address: Optional[str] = None,
        borough: Optional[str] = None,
        block: Optional[str] = None,
        lot: Optional[str] = None,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        error: Optional[str] = None,
    ):
        self.bbl = bbl
        self.bin_number = bin_number
        self.normalized_address = normalized_address
        self.borough = borough
        self.block = block
        self.lot = lot
        self.latitude = latitude
        self.longitude = longitude
        self.error = error

    @property
    def is_valid(self) -> bool:
        """Check if geocoding was successful."""
        return self.bbl is not None and self.error is None


class NYCGeocoder:
    """Geocoder for NYC addresses using Planning Labs GeoSearch."""

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=15.0)
        return self._client

    def _get_borough_name(self, borough: Borough) -> str:
        """Convert Borough enum to search string."""
        mapping = {
            Borough.MANHATTAN: "Manhattan",
            Borough.BRONX: "Bronx",
            Borough.BROOKLYN: "Brooklyn",
            Borough.QUEENS: "Queens",
            Borough.STATEN_ISLAND: "Staten Island",
        }
        return mapping[borough]

    async def lookup(
        self,
        house_number: str,
        street: str,
        borough: Borough,
    ) -> GeocoderResult:
        """
        Look up BBL for an NYC address.

        Args:
            house_number: Property house number
            street: Street name
            borough: NYC borough

        Returns:
            GeocoderResult with BBL and property info
        """
        try:
            client = await self._get_client()

            # Build search query
            borough_name = self._get_borough_name(borough)
            query = f"{house_number} {street}, {borough_name}, NY"

            logger.info(f"Geocoding address: {query}")

            # Make request to GeoSearch API
            response = await client.get(
                GEOSEARCH_BASE_URL,
                params={"text": query},
            )

            response.raise_for_status()
            data = response.json()

            # Parse response
            features = data.get("features", [])

            if not features:
                logger.warning(f"No geocoding results for: {query}")
                return GeocoderResult(error="Address not found")

            # Get first (best) match
            feature = features[0]
            properties = feature.get("properties", {})
            geometry = feature.get("geometry", {})
            coordinates = geometry.get("coordinates", [None, None])

            # Extract BBL components (nested under addendum.pad in v2 API)
            addendum = properties.get("addendum", {})
            pad_data = addendum.get("pad", {})
            pad_bbl = pad_data.get("bbl", "")
            pad_bin = pad_data.get("bin", "")

            # Parse BBL (format: BBBBBBBLL - 10 digits)
            bbl = pad_bbl if pad_bbl else None
            borough_code = pad_bbl[0] if len(pad_bbl) >= 1 else None
            block = pad_bbl[1:6] if len(pad_bbl) >= 6 else None
            lot = pad_bbl[6:10] if len(pad_bbl) >= 10 else None

            result = GeocoderResult(
                bbl=bbl,
                bin_number=pad_bin if pad_bin else None,
                normalized_address=properties.get("label", ""),
                borough=properties.get("borough", ""),
                block=block,
                lot=lot,
                latitude=coordinates[1] if len(coordinates) > 1 else None,
                longitude=coordinates[0] if len(coordinates) > 0 else None,
            )

            logger.info(f"Geocoded to BBL: {result.bbl}")
            return result

        except httpx.HTTPStatusError as e:
            logger.error(f"Geocoding HTTP error: {e}")
            return GeocoderResult(error=f"HTTP error: {e.response.status_code}")

        except Exception as e:
            logger.error(f"Geocoding error: {e}")
            return GeocoderResult(error=str(e))

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None


# Singleton instance
_geocoder_instance: Optional[NYCGeocoder] = None


def get_geocoder() -> NYCGeocoder:
    """Get the singleton geocoder instance."""
    global _geocoder_instance
    if _geocoder_instance is None:
        _geocoder_instance = NYCGeocoder()
    return _geocoder_instance
