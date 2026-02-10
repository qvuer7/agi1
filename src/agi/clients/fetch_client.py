"""HTTP fetch client for web pages."""

import httpx
from typing import Any, Dict, Optional

from ..config import USER_AGENT, HTTP_TIMEOUT, HTTP_MAX_REDIRECTS
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
            Dict with: {status, final_url, html, headers}
        """
        try:
            response = self.client.get(url)
            response.raise_for_status()

            result = {
                "status": response.status_code,
                "final_url": str(response.url),
                "html": response.text,
                "headers": dict(response.headers),
            }

            logger.info(f"Fetched {url} -> {result['final_url']} ({result['status']})")
            return result

        except httpx.HTTPStatusError as e:
            logger.warning(f"HTTP error fetching {url}: {e.response.status_code}")
            return {
                "status": e.response.status_code,
                "final_url": str(e.response.url) if e.response else url,
                "html": "",
                "headers": dict(e.response.headers) if e.response else {},
            }
        except httpx.TimeoutException:
            logger.warning(f"Timeout fetching {url}")
            return {
                "status": 0,
                "final_url": url,
                "html": "",
                "headers": {},
            }
        except Exception as e:
            logger.error(f"Error fetching {url}: {e}")
            return {
                "status": 0,
                "final_url": url,
                "html": "",
                "headers": {},
            }

    def __del__(self):
        """Close HTTP client on cleanup."""
        if hasattr(self, "client"):
            self.client.close()
