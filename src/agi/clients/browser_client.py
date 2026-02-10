"""Playwright browser client for rendering JavaScript-heavy pages."""

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from playwright.sync_api import sync_playwright, Browser, Page, TimeoutError as PlaywrightTimeoutError
from typing import Dict, Optional

from ..config import (
    BROWSER_HEADLESS,
    BROWSER_TIMEOUT,
    BROWSER_NETWORK_IDLE_TIMEOUT,
)
from ..extract.html_extract import extract_text, extract_links
from ..extract.page_classifier import classify_page
from ..logging import get_logger

logger = get_logger(__name__)

# Thread pool for running sync Playwright in async context
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="playwright")


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

    def _render_sync(self, url: str) -> Dict[str, str]:
        """
        Render a web page using Playwright (sync version, runs in thread).

        Args:
            url: URL to render

        Returns:
            Dict with: {html, text}
        """
        start_time = time.time()
        logger.info(f"[RENDER] Starting render of {url}")
        
        self._ensure_initialized()

        if not self.browser:
            raise RuntimeError("Browser not initialized")

        try:
            page: Page = self.browser.new_page()
            logger.debug(f"[RENDER] Created new page for {url}")
            # Set a more reasonable default timeout
            page.set_default_timeout(BROWSER_TIMEOUT)

            # Navigate with "load" state (more lenient than networkidle)
            # networkidle is too strict for many sites that keep making requests
            navigation_success = False
            nav_start = time.time()
            try:
                logger.debug(f"[RENDER] Navigating to {url} (wait_until=load, timeout={BROWSER_TIMEOUT}ms)")
                page.goto(url, wait_until="load", timeout=BROWSER_TIMEOUT)
                nav_time = (time.time() - nav_start) * 1000
                logger.info(f"[RENDER] Navigation successful for {url} (took {nav_time:.0f}ms)")
                navigation_success = True
            except PlaywrightTimeoutError:
                nav_time = (time.time() - nav_start) * 1000
                logger.warning(f"[RENDER] Page load timeout for {url} after {nav_time:.0f}ms, waiting for domcontentloaded")
                # If load times out, the page might still be loading
                # Wait for domcontentloaded on the current navigation
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=10000)
                    nav_time = (time.time() - nav_start) * 1000
                    logger.info(f"[RENDER] DOMContentLoaded reached for {url} (took {nav_time:.0f}ms)")
                    navigation_success = True
                except PlaywrightTimeoutError:
                    nav_time = (time.time() - nav_start) * 1000
                    logger.warning(f"[RENDER] DOMContentLoaded timeout for {url} after {nav_time:.0f}ms, will try to get partial content")
                    # Don't fail yet - try to get whatever content is available

            # Try to wait for network idle, but don't fail if it times out
            try:
                logger.debug(f"[RENDER] Waiting for networkidle for {url} (timeout={BROWSER_NETWORK_IDLE_TIMEOUT}ms)")
                page.wait_for_load_state("networkidle", timeout=BROWSER_NETWORK_IDLE_TIMEOUT)
                logger.debug(f"[RENDER] Network idle reached for {url}")
            except PlaywrightTimeoutError:
                logger.debug(f"[RENDER] Network idle timeout for {url}, continuing with current content")

            # Capture final URL and canonical
            final_url = page.url
            canonical_url = None
            try:
                canonical_element = page.query_selector('link[rel="canonical"]')
                if canonical_element:
                    canonical_href = canonical_element.get_attribute("href")
                    if canonical_href:
                        from urllib.parse import urljoin
                        canonical_url = urljoin(final_url, canonical_href)
                        logger.debug(f"[RENDER] Found canonical URL: {canonical_url}")
            except Exception as e:
                logger.debug(f"[RENDER] Could not extract canonical URL: {e}")
            
            # Get content (even if navigation timed out, we might have partial content)
            content_start = time.time()
            html = page.content()
            text = page.inner_text("body") if page.query_selector("body") else ""
            content_time = (time.time() - content_start) * 1000
            logger.debug(f"[RENDER] Extracted content from {url} (took {content_time:.0f}ms, {len(html)} bytes HTML, {len(text)} chars text)")
            logger.info(f"[RENDER] Final URL: {final_url}, Canonical: {canonical_url or 'none'}")
            
            # If we got no content, something went wrong
            if not html or len(html) < 100:
                page.close()
                total_time = (time.time() - start_time) * 1000
                logger.warning(f"[RENDER] No content received for {url} (total time: {total_time:.0f}ms)")
                return {
                    "html": "",
                    "text": "",
                    "extracted_links": [],
                    "classification": {
                        "verdict": "error",
                        "product_count": 0,
                        "reason": "No content received (timeout or error)",
                        "signals": {},
                    },
                    "final_url": url,
                    "canonical_url": None,
                }
            
            # Extract text and links (if not already extracted)
            extract_start = time.time()
            if not text:
                text = extract_text(html)
            
            extracted_links = extract_links(html, url)
            extract_time = (time.time() - extract_start) * 1000
            logger.debug(f"[RENDER] Extracted {len(extracted_links)} links from {url} (took {extract_time:.0f}ms)")
            
            # Classify page
            classify_start = time.time()
            classification = classify_page(html, text, url)
            classify_time = (time.time() - classify_start) * 1000
            product_candidates = classification.get("product_candidate_links", [])
            logger.debug(f"[RENDER] Classified {url} as {classification['verdict']} with {len(product_candidates)} product candidates (took {classify_time:.0f}ms)")

            page.close()
            
            total_time = (time.time() - start_time) * 1000
            status_msg = "fully loaded" if navigation_success else "partial content (timeout)"
            logger.info(f"[RENDER] Completed {url} ({status_msg}, {len(html)} bytes HTML, {len(text)} chars text, {len(product_candidates)} product candidates) - {classification['verdict']} (total: {total_time:.0f}ms)")
            return {
                "html": html,
                "text": text,
                "extracted_links": extracted_links,
                "classification": classification,
                "final_url": final_url,
                "canonical_url": canonical_url,
            }

        except PlaywrightTimeoutError as e:
            total_time = (time.time() - start_time) * 1000
            logger.warning(f"[RENDER] Timeout rendering {url}: {e} (total time: {total_time:.0f}ms)")
            # Try to get partial content even on timeout
            try:
                if 'page' in locals() and page:
                    html = page.content()
                    text = page.inner_text("body") if page.query_selector("body") else ""
                    if html and len(html) > 100:  # If we got some content, use it
                        logger.info(f"[RENDER] Got partial content for {url} despite timeout ({len(html)} bytes)")
                        extracted_links = extract_links(html, url)
                        classification = classify_page(html, text, url)
                        page.close()
                        return {
                            "html": html,
                            "text": text,
                            "extracted_links": extracted_links,
                            "classification": classification,
                        }
            except Exception as partial_error:
                logger.debug(f"[RENDER] Could not get partial content: {partial_error}")
            
            return {
                "html": "",
                "text": "",
                "extracted_links": [],
                "classification": {
                    "verdict": "error",
                    "product_count": 0,
                    "reason": f"Timeout: {str(e)}",
                    "signals": {},
                },
                "final_url": url,
                "canonical_url": None,
            }
        except Exception as e:
            total_time = (time.time() - start_time) * 1000
            logger.error(f"[RENDER] Error rendering {url}: {e} (total time: {total_time:.0f}ms)", exc_info=True)
            return {
                "html": "",
                "text": "",
                "extracted_links": [],
                "classification": {
                    "verdict": "error",
                    "product_count": 0,
                    "reason": str(e),
                    "signals": {},
                },
                "final_url": url,
                "canonical_url": None,
            }

    def render(self, url: str) -> Dict[str, str]:
        """
        Render a web page using Playwright (async-safe wrapper).

        Args:
            url: URL to render

        Returns:
            Dict with: {html, text}
        """
        # Run sync Playwright code in thread pool to avoid asyncio conflict
        try:
            loop = asyncio.get_running_loop()
            # We're in an async context, run in thread pool
            import concurrent.futures
            future = _executor.submit(self._render_sync, url)
            # Wait for result (blocking call, but necessary to avoid asyncio conflict)
            return future.result(timeout=BROWSER_TIMEOUT / 1000 + 10)  # Add buffer for timeout
        except RuntimeError:
            # No event loop running, run directly
            return self._render_sync(url)
        except concurrent.futures.TimeoutError:
            logger.error(f"Timeout waiting for render of {url} (thread pool timeout)")
            return {
                "html": "",
                "text": "",
                "extracted_links": [],
                "classification": {
                    "verdict": "error",
                    "product_count": 0,
                    "reason": "Thread pool timeout",
                    "signals": {},
                },
            }

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
