"""
NYC Department of Buildings BIS Web Scraper.

Scrapes property data from DOB Building Information System (BIS) web portal
to extract violations, stop work orders, and vacate orders.

Includes circuit breaker pattern for resilience when DOB is unavailable.
"""

import asyncio
import logging
import random
import re
import time
from datetime import datetime, timezone
from typing import Optional, List

from bs4 import BeautifulSoup
from playwright.async_api import TimeoutError as PlaywrightTimeout

from ..browser_manager import get_browser_manager, USER_AGENTS
from ..config import get_settings
from ..models import DOBStatus, Borough, TimelineEvent, EventSource

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """
    Circuit breaker for DOB scraper resilience.

    States:
    - CLOSED: Normal operation
    - OPEN: Failing, skip requests for cooldown period
    - HALF_OPEN: Testing if service recovered
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        cooldown_seconds: int = 300,  # 5 minutes
    ):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.consecutive_failures = 0
        self.last_failure_time: Optional[float] = None
        self.state = "CLOSED"

    def record_success(self) -> None:
        """Record a successful request."""
        self.consecutive_failures = 0
        self.state = "CLOSED"
        logger.debug("Circuit breaker: recorded success, state=CLOSED")

    def record_failure(self) -> None:
        """Record a failed request."""
        self.consecutive_failures += 1
        self.last_failure_time = time.time()

        if self.consecutive_failures >= self.failure_threshold:
            self.state = "OPEN"
            logger.warning(
                f"Circuit breaker OPEN: {self.consecutive_failures} consecutive failures. "
                f"Skipping DOB for {self.cooldown_seconds}s"
            )

    def is_available(self) -> bool:
        """Check if service should be called."""
        if self.state == "CLOSED":
            return True

        if self.state == "OPEN":
            # Check if cooldown has passed
            if self.last_failure_time:
                elapsed = time.time() - self.last_failure_time
                if elapsed >= self.cooldown_seconds:
                    self.state = "HALF_OPEN"
                    logger.info("Circuit breaker: cooldown passed, state=HALF_OPEN")
                    return True
            return False

        # HALF_OPEN: allow one request through
        return True

    def get_status(self) -> dict:
        """Get circuit breaker status."""
        return {
            "state": self.state,
            "consecutive_failures": self.consecutive_failures,
            "cooldown_remaining": max(
                0,
                self.cooldown_seconds - (time.time() - (self.last_failure_time or 0))
            ) if self.state == "OPEN" else 0,
        }


# Global circuit breaker instance
_circuit_breaker = CircuitBreaker()


def get_circuit_breaker() -> CircuitBreaker:
    """Get the DOB circuit breaker instance."""
    return _circuit_breaker


class DOBScraper:
    """Scraper for NYC DOB Building Information System."""

    def __init__(self):
        self._settings = get_settings()

    def _get_borough_code(self, borough: Borough) -> str:
        """Convert Borough enum to DOB BIS borough code."""
        codes = {
            Borough.MANHATTAN: "1",
            Borough.BRONX: "2",
            Borough.BROOKLYN: "3",
            Borough.QUEENS: "4",
            Borough.STATEN_ISLAND: "5",
        }
        return codes[borough]

    def _build_search_url(
        self,
        house_number: str,
        street: str,
        borough: Borough,
    ) -> str:
        """Build DOB BIS property search URL."""
        base_url = self._settings.dob_bis_base_url
        borough_code = self._get_borough_code(borough)

        # Clean inputs
        house_num = house_number.strip().upper()
        street_name = street.strip().upper()

        # Build URL for property profile overview
        # The DOB BIS uses a servlet that accepts address parameters
        url = (
            f"{base_url}/PropertyProfileOverviewServlet?"
            f"boro={borough_code}&"
            f"houseno={house_num}&"
            f"street={street_name.replace(' ', '+')}"
        )

        return url

    async def _extract_data_from_page(
        self,
        html_content: str,
    ) -> DOBStatus:
        """
        Extract DOB data from the property profile HTML.

        Args:
            html_content: Raw HTML from DOB BIS page

        Returns:
            DOBStatus with extracted data
        """
        soup = BeautifulSoup(html_content, "lxml")
        status = DOBStatus()

        try:
            # Extract BIN (Building Identification Number)
            bin_match = re.search(r"BIN#?\s*:?\s*(\d{7})", html_content, re.IGNORECASE)
            if bin_match:
                status.bin_number = bin_match.group(1)

            # Look for violations section
            # DOB BIS typically shows "Open Violations" count
            violations_pattern = re.compile(
                r"(?:open|active)\s*violations?\s*[:=]?\s*(\d+)",
                re.IGNORECASE
            )
            violations_match = violations_pattern.search(html_content)
            if violations_match:
                status.open_violations = int(violations_match.group(1))

            # Alternative: Count violation rows in table
            violation_tables = soup.find_all("table", string=re.compile(r"violation", re.I))
            if not violations_match and violation_tables:
                # Count table rows that appear to be violations
                for table in violation_tables:
                    rows = table.find_all("tr")
                    # Subtract header row
                    status.open_violations = max(0, len(rows) - 1)

            # Check for Stop Work Order
            swo_patterns = [
                r"stop\s*work\s*order",
                r"SWO",
                r"work\s*stop\s*order",
            ]
            for pattern in swo_patterns:
                if re.search(pattern, html_content, re.IGNORECASE):
                    # Check if it's marked as active/yes
                    swo_section = re.search(
                        rf"({pattern})[^\n]{{0,100}}(active|yes|in\s*effect)",
                        html_content,
                        re.IGNORECASE,
                    )
                    if swo_section:
                        status.stop_work_order = True
                        break
                    # Also check for SWO in a table or listing
                    if re.search(rf"<td[^>]*>[^<]*{pattern}", html_content, re.IGNORECASE):
                        status.stop_work_order = True
                        break

            # Check for Vacate Order
            vacate_patterns = [
                r"vacate\s*order",
                r"full\s*vacate",
                r"partial\s*vacate",
                r"vacate.*active",
            ]
            for pattern in vacate_patterns:
                if re.search(pattern, html_content, re.IGNORECASE):
                    vacate_section = re.search(
                        rf"({pattern})[^\n]{{0,100}}(active|yes|in\s*effect)",
                        html_content,
                        re.IGNORECASE,
                    )
                    if vacate_section:
                        status.vacate_order = True
                        break
                    # Check table cells
                    if re.search(rf"<td[^>]*>[^<]*{pattern}", html_content, re.IGNORECASE):
                        status.vacate_order = True
                        break

            # Additional check: look for specific DOB BIS page elements
            # that indicate active orders
            active_order_indicators = [
                soup.find(string=re.compile(r"stop\s*work.*active", re.I)),
                soup.find(string=re.compile(r"vacate.*active", re.I)),
                soup.find("td", class_="status", string=re.compile(r"SWO|vacate", re.I)),
            ]

            for indicator in active_order_indicators:
                if indicator:
                    text = str(indicator).lower()
                    if "stop" in text or "swo" in text:
                        status.stop_work_order = True
                    if "vacate" in text:
                        status.vacate_order = True

            status.scraped_at = datetime.now(timezone.utc)

        except Exception as e:
            logger.error(f"Error parsing DOB HTML: {e}")
            status.error = f"Parse error: {str(e)}"

        return status

    async def get_dob_status(
        self,
        house_number: str,
        street: str,
        borough: Borough,
    ) -> DOBStatus:
        """
        Scrape DOB BIS for property status.

        Implements retry logic with different user agents on failure.
        Uses circuit breaker to handle extended DOB unavailability.

        Args:
            house_number: Property house number
            street: Street name
            borough: NYC borough

        Returns:
            DOBStatus with scraped data or error information
        """
        # Check circuit breaker
        circuit_breaker = get_circuit_breaker()
        if not circuit_breaker.is_available():
            logger.warning("DOB circuit breaker OPEN - skipping scrape")
            return DOBStatus(
                error="DOB temporarily unavailable (circuit breaker open)",
                scraped_at=datetime.now(timezone.utc),
            )

        url = self._build_search_url(house_number, street, borough)
        logger.info(f"Scraping DOB BIS: {url}")

        browser_manager = await get_browser_manager()
        last_error: Optional[str] = None
        used_agents: set = set()
        success = False

        # Retry loop with different user agents
        for attempt in range(self._settings.dob_retry_count + 1):
            try:
                # Select a different user agent for each retry attempt
                available_agents = [ua for ua in USER_AGENTS if ua not in used_agents]
                if not available_agents:
                    available_agents = list(USER_AGENTS)
                user_agent = random.choice(available_agents)
                used_agents.add(user_agent)
                logger.debug(f"Attempt {attempt + 1} using User-Agent: {user_agent[:50]}...")

                async with browser_manager.get_page(user_agent=user_agent) as page:
                    # Navigate to the search URL
                    response = await page.goto(
                        url,
                        wait_until="networkidle",
                        timeout=self._settings.dob_scrape_timeout_ms,
                    )

                    # Check for HTTP errors
                    if response and response.status >= 400:
                        raise Exception(f"HTTP {response.status}")

                    # Wait for content to load
                    await page.wait_for_load_state("domcontentloaded")

                    # Small delay for dynamic content
                    await asyncio.sleep(1)

                    # Get page content
                    html_content = await page.content()

                    # Check for "no results" or error messages
                    if "no records found" in html_content.lower():
                        logger.warning(f"No DOB records found for address")
                        return DOBStatus(
                            error="No property records found",
                            scraped_at=datetime.now(timezone.utc),
                        )

                    # Extract data from HTML
                    status = await self._extract_data_from_page(html_content)

                    if status.error is None:
                        logger.info(
                            f"DOB scrape successful: violations={status.open_violations}, "
                            f"SWO={status.stop_work_order}, vacate={status.vacate_order}"
                        )
                        circuit_breaker.record_success()
                        return status

            except PlaywrightTimeout as e:
                last_error = f"Timeout on attempt {attempt + 1}"
                logger.warning(f"DOB scrape timeout (attempt {attempt + 1}): {e}")

            except Exception as e:
                last_error = str(e)
                logger.warning(f"DOB scrape error (attempt {attempt + 1}): {e}")

            # Wait before retry
            if attempt < self._settings.dob_retry_count:
                await asyncio.sleep(2)

        # All retries failed - record failure in circuit breaker
        circuit_breaker.record_failure()
        logger.error(f"DOB scrape failed after {self._settings.dob_retry_count + 1} attempts")
        return DOBStatus(
            error=f"Scrape failed: {last_error}",
            scraped_at=datetime.now(timezone.utc),
        )

    async def get_violation_history(
        self,
        house_number: str,
        street: str,
        borough: Borough,
    ) -> List[TimelineEvent]:
        """
        Scrape DOB BIS for violation history.

        Args:
            house_number: Property house number
            street: Street name
            borough: NYC borough

        Returns:
            List of TimelineEvent objects for DOB violations
        """
        base_url = self._settings.dob_bis_base_url
        borough_code = self._get_borough_code(borough)
        house_num = house_number.strip().upper()
        street_name = street.strip().upper()

        # Build URL for violations page
        url = (
            f"{base_url}/ECBQueryByLocationServlet?"
            f"boro={borough_code}&"
            f"houseno={house_num}&"
            f"street={street_name.replace(' ', '+')}"
        )

        logger.info(f"Scraping DOB violation history: {url}")

        browser_manager = await get_browser_manager()
        events: List[TimelineEvent] = []

        try:
            async with browser_manager.get_page() as page:
                response = await page.goto(
                    url,
                    wait_until="networkidle",
                    timeout=self._settings.dob_scrape_timeout_ms,
                )

                if response and response.status >= 400:
                    logger.warning(f"DOB violation history returned HTTP {response.status}")
                    return events

                await page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(1)

                html_content = await page.content()
                soup = BeautifulSoup(html_content, "lxml")

                # Find violation tables
                tables = soup.find_all("table")

                for table in tables:
                    rows = table.find_all("tr")

                    for row in rows[1:]:  # Skip header row
                        cells = row.find_all("td")
                        if len(cells) >= 3:
                            # Try to extract date, type, status from cells
                            date_str = "Unknown"
                            event_type = "Violation"
                            status = ""
                            description = ""

                            for i, cell in enumerate(cells):
                                text = cell.get_text(strip=True)

                                # Try to detect date patterns
                                date_match = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", text)
                                if date_match:
                                    try:
                                        parsed = datetime.strptime(date_match.group(1), "%m/%d/%Y")
                                        date_str = parsed.strftime("%Y-%m-%d")
                                    except ValueError:
                                        pass

                                # Detect violation type
                                if any(kw in text.lower() for kw in ["ecb", "violation", "dob"]):
                                    event_type = text[:50] if len(text) <= 50 else text[:47] + "..."

                                # Detect status
                                if any(kw in text.lower() for kw in ["open", "closed", "active", "resolved"]):
                                    status = text

                                # Build description from other cells
                                if text and len(text) > 5 and not date_match:
                                    if description:
                                        description += " | " + text[:50]
                                    else:
                                        description = text[:100]

                            if date_str != "Unknown" or description:
                                events.append(TimelineEvent(
                                    date=date_str,
                                    source=EventSource.DOB,
                                    event_type=event_type,
                                    description=description[:200] if description else None,
                                    status=status or None,
                                ))

                logger.info(f"Found {len(events)} DOB violation history events")

        except PlaywrightTimeout:
            logger.warning("DOB violation history scrape timed out")
        except Exception as e:
            logger.error(f"Error scraping DOB violation history: {e}")

        return events


# Singleton instance
_scraper_instance: Optional[DOBScraper] = None


def get_dob_scraper() -> DOBScraper:
    """Get the singleton DOB scraper instance."""
    global _scraper_instance
    if _scraper_instance is None:
        _scraper_instance = DOBScraper()
    return _scraper_instance
