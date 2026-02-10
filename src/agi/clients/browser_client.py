"""Playwright browser client for rendering JavaScript-heavy pages."""

from playwright.sync_api import sync_playwright, Browser, Page, TimeoutError as PlaywrightTimeoutError
from typing import Dict, Optional

from ..config import (
    BROWSER_HEADLESS,
    BROWSER_TIMEOUT,
    BROWSER_NETWORK_IDLE_TIMEOUT,
)
from ..logging import get_logger

logger = get_logger(__name__)


class BrowserClient:
    """Client for rendering web pages with Playwright."""

    def __init__(self):
        self.playwright = None
        self.browser: Optional[Browser] = None
        self._initialized = False

    def _ensure_initialized(self):
        """Initialize Playwright and browser if not already done."""
        if not self._initialized:
            self.playwright = sync_playwright().start()
            self.browser = self.playwright.chromium.launch(headless=BROWSER_HEADLESS)
            self._initialized = True

    def render(self, url: str) -> Dict[str, str]:
        """
        Render a web page using Playwright.

        Args:
            url: URL to render

        Returns:
            Dict with: {html, text}
        """
        self._ensure_initialized()

        if not self.browser:
            raise RuntimeError("Browser not initialized")

        try:
            page: Page = self.browser.new_page()
            page.set_default_timeout(BROWSER_TIMEOUT)

            # Navigate and wait for network idle
            page.goto(url, wait_until="networkidle", timeout=BROWSER_TIMEOUT)

            # Additional wait for network idle
            try:
                page.wait_for_load_state("networkidle", timeout=BROWSER_NETWORK_IDLE_TIMEOUT)
            except PlaywrightTimeoutError:
                logger.warning(f"Network idle timeout for {url}, continuing anyway")

            # Get content
            html = page.content()
            text = page.inner_text("body") if page.query_selector("body") else ""

            page.close()

            logger.info(f"Rendered {url} ({len(html)} bytes HTML, {len(text)} chars text)")
            return {"html": html, "text": text}

        except PlaywrightTimeoutError:
            logger.warning(f"Timeout rendering {url}")
            return {"html": "", "text": ""}
        except Exception as e:
            logger.error(f"Error rendering {url}: {e}")
            return {"html": "", "text": ""}

    def close(self):
        """Close browser and cleanup."""
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()
        self._initialized = False

    def __del__(self):
        """Cleanup on deletion."""
        self.close()
