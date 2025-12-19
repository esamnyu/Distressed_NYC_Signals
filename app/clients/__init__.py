"""
External Data Clients Module.

Provides async clients for fetching property data from NYC OpenData APIs:
- NYC311Client: 311 complaints (illegal conversions, heat/water, noise)
- HPDClient: Housing Preservation & Development violations (Class A/B/C)

All clients use the Socrata API with configurable timeouts and
include SoQL injection protection.
"""

from .nyc_311_client import NYC311Client, get_311_client
from .hpd_client import HPDClient, get_hpd_client

__all__ = ["NYC311Client", "get_311_client", "HPDClient", "get_hpd_client"]
