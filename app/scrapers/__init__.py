"""
Web Scrapers Module.

Provides Playwright-based scrapers for data not available via API:
- DOBScraper: NYC Department of Buildings violations, Stop Work Orders,
              and Vacate Orders from the BIS (Building Information System)

Scrapers include circuit breaker patterns for resilience and
anti-detection measures for reliable scraping.
"""

from .dob_scraper import DOBScraper, get_dob_scraper

__all__ = ["DOBScraper", "get_dob_scraper"]
