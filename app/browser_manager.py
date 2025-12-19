"""
Browser Pool Singleton for Playwright.

Maintains a persistent Chromium browser instance to reduce latency
by avoiding the 1-2s startup time for each request.
"""

import asyncio
import logging
import random
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

from playwright.async_api import Browser, BrowserContext, Page, async_playwright, Playwright

from .config import get_settings

logger = logging.getLogger(__name__)


# User agents for rotation on retries
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]


class BrowserManager:
    """
    Singleton browser pool manager for Playwright.

    Maintains a persistent headless Chromium instance and provides
    context/page management for concurrent scraping requests.
    """

    _instance: Optional["BrowserManager"] = None
    _lock: asyncio.Lock = asyncio.Lock()

    def __init__(self):
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._initialized: bool = False
        self._settings = get_settings()

    @classmethod
    async def get_instance(cls) -> "BrowserManager":
        """Get or create the singleton instance."""
        async with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            if not cls._instance._initialized:
                await cls._instance.initialize()
            return cls._instance

    async def initialize(self) -> None:
        """Initialize the browser instance."""
        if self._initialized:
            return

        logger.info("Initializing Playwright browser...")

        try:
            self._playwright = await async_playwright().start()

            # Launch Chromium with anti-detection flags
            self._browser = await self._playwright.chromium.launch(
                headless=self._settings.browser_headless,
                args=self._settings.browser_args,
            )

            self._initialized = True
            logger.info("Browser initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize browser: {e}")
            raise

    async def close(self) -> None:
        """Close the browser and cleanup resources."""
        logger.info("Closing browser...")

        if self._browser:
            try:
                await self._browser.close()
            except Exception as e:
                logger.warning(f"Error closing browser: {e}")
            self._browser = None

        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception as e:
                logger.warning(f"Error stopping playwright: {e}")
            self._playwright = None

        self._initialized = False
        logger.info("Browser closed")

    @property
    def is_ready(self) -> bool:
        """Check if browser is ready for requests."""
        return self._initialized and self._browser is not None

    def get_random_user_agent(self) -> str:
        """Get a random user agent for anti-detection."""
        return random.choice(USER_AGENTS)

    @asynccontextmanager
    async def get_page(
        self,
        user_agent: Optional[str] = None,
    ) -> AsyncGenerator[Page, None]:
        """
        Get a new browser page in an isolated context.

        The context and page are automatically closed after use to prevent
        memory leaks. Each request gets a fresh context with no shared cookies.

        Args:
            user_agent: Optional user agent string. Random if not provided.

        Yields:
            A Playwright Page object.
        """
        if not self.is_ready:
            await self.initialize()

        if user_agent is None:
            user_agent = self.get_random_user_agent()

        # Create isolated context for this request
        context: Optional[BrowserContext] = None
        page: Optional[Page] = None

        try:
            context = await self._browser.new_context(
                user_agent=user_agent,
                viewport={"width": 1920, "height": 1080},
                java_script_enabled=True,
                ignore_https_errors=False,  # Enforce HTTPS validation for security
                extra_http_headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.5",
                    "Accept-Encoding": "gzip, deflate",
                    "DNT": "1",
                    "Connection": "keep-alive",
                    "Upgrade-Insecure-Requests": "1",
                },
            )

            # Inject stealth scripts to evade bot detection
            await context.add_init_script("""
                // Remove webdriver property
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined,
                });

                // Mock plugins
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5],
                });

                // Mock languages
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['en-US', 'en'],
                });

                // Override chrome property
                window.chrome = {
                    runtime: {},
                };

                // Mock permissions
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications' ?
                        Promise.resolve({ state: Notification.permission }) :
                        originalQuery(parameters)
                );
            """)

            page = await context.new_page()

            yield page

        finally:
            # Clean up: close page and context to prevent memory leaks
            if page:
                try:
                    await page.close()
                except Exception as e:
                    logger.warning(f"Error closing page: {e}")

            if context:
                try:
                    await context.close()
                except Exception as e:
                    logger.warning(f"Error closing context: {e}")


# Global instance getter
async def get_browser_manager() -> BrowserManager:
    """Get the global browser manager instance."""
    return await BrowserManager.get_instance()
