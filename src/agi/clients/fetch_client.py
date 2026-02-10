"""HTTP fetch client for web pages."""

import httpx
from typing import Any, Dict, Optional
from bs4 import BeautifulSoup

from ..config import USER_AGENT, HTTP_TIMEOUT, HTTP_MAX_REDIRECTS
from ..extract.html_extract import extract_text, extract_links
from ..extract.page_classifier import classify_page
from ..logging import get_logger

logger = get_logger(__name__)


class FetchClient:
    """Client for fetching web pages via HTTP."""

    def __init__(self):
        self.client = httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=HTTP_TIMEOUT,
            follow_redirects=True,
            max_redirects=HTTP_MAX_REDIRECTS,
        )

    def fetch(self, url: str) -> Dict[str, Any]:
        """
        Fetch a web page.

        Args:
            url: URL to fetch

        Returns:
            Dict with: {status, final_url, html, headers, title, text, error}
        """
        try:
            response = self.client.get(url)
            response.raise_for_status()

            html = response.text
            title = self._extract_title(html)
            final_url = str(response.url)
            
            # Extract canonical URL from HTML
            canonical_url = None
            try:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html, "lxml")
                canonical_tag = soup.find("link", rel="canonical")
                if canonical_tag and canonical_tag.get("href"):
                    from urllib.parse import urljoin
                    canonical_url = urljoin(final_url, canonical_tag["href"])
                    logger.debug(f"Found canonical URL: {canonical_url}")
            except Exception as e:
                logger.debug(f"Could not extract canonical URL: {e}")
            
            # Extract text and links
            text = extract_text(html)
            extracted_links = extract_links(html, final_url)
            
            # Classify page
            classification = classify_page(html, text, final_url)

            result = {
                "status": response.status_code,
                "final_url": final_url,
                "canonical_url": canonical_url,
                "html": html,
                "headers": dict(response.headers),
                "title": title,
                "text": text,
                "extracted_links": extracted_links,
                "classification": classification,
                "error": None,
            }

            logger.info(f"Fetched {url} -> {final_url} ({response.status_code}) - {classification['verdict']}")
            return result

        except httpx.HTTPStatusError as e:
            error_msg = f"HTTP {e.response.status_code}"
            logger.warning(f"HTTP error fetching {url}: {e.response.status_code}")
            return {
                "status": e.response.status_code,
                "final_url": str(e.response.url) if e.response else url,
                "html": "",
                "headers": dict(e.response.headers) if e.response else {},
                "title": "",
                "text": "",
                "error": error_msg,
            }
        except httpx.TimeoutException:
            logger.warning(f"Timeout fetching {url}")
            return {
                "status": 0,
                "final_url": url,
                "html": "",
                "headers": {},
                "title": "",
                "text": "",
                "error": "Timeout",
            }
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error fetching {url}: {e}")
            return {
                "status": 0,
                "final_url": url,
                "html": "",
                "headers": {},
                "title": "",
                "text": "",
                "error": error_msg,
            }

    def _extract_title(self, html: str) -> str:
        """Extract title from HTML."""
        if not html:
            return ""
        try:
            soup = BeautifulSoup(html, "lxml")
            title_tag = soup.find("title")
            if title_tag:
                return title_tag.get_text(strip=True)
        except Exception:
            pass
        return ""

    def __del__(self):
        """Close HTTP client on cleanup."""
        if hasattr(self, "client"):
            self.client.close()
