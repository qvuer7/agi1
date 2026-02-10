"""Brave Search API client."""

import time
import httpx
from typing import Dict, List, Optional

from ..config import BRAVE_API_KEY, BRAVE_BASE_URL
from ..logging import get_logger

logger = get_logger(__name__)


class BraveClient:
    """Client for Brave Search API."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or BRAVE_API_KEY
        self.base_url = BRAVE_BASE_URL
        
        # Note: We don't validate here because Brave API key is only needed when search is called
        # This allows the service to start even if Brave key is missing (will fail on first search)
        
        self.client = httpx.Client(
            base_url=self.base_url,
            headers={
                "X-Subscription-Token": self.api_key or "",
            },
            timeout=30.0,
        )

    def search(self, query: str, count: int = 5) -> List[Dict[str, str]]:
        """
        Search the web using Brave Search.

        Args:
            query: Search query string
            count: Number of results to return (max 20)

        Returns:
            List of normalized result dicts: {title, url, snippet}
        """
        if not self.api_key:
            raise ValueError("BRAVE_API_KEY not configured")

        count = min(count, 20)  # Brave API limit

        try:
            params = {
                "q": query,
                "count": count,
            }
            
            # Retry with exponential backoff for 429 errors
            max_retries = 3
            base_delay = 1.0
            
            for attempt in range(max_retries):
                response = self.client.get("/web/search", params=params)
                
                if response.status_code == 429:
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)
                        logger.warning(f"Brave API rate limited (429), retrying in {delay}s (attempt {attempt + 1}/{max_retries})")
                        time.sleep(delay)
                        continue
                    else:
                        response.raise_for_status()
                else:
                    response.raise_for_status()
                    break
            
            data = response.json()

            results = []
            if "web" in data and "results" in data["web"]:
                for item in data["web"]["results"]:
                    results.append({
                        "title": item.get("title", ""),
                        "url": item.get("url", ""),
                        "snippet": item.get("description", ""),
                    })

            logger.info(f"Brave search '{query}' returned {len(results)} results")
            return results

        except httpx.HTTPStatusError as e:
            logger.error(f"Brave API error: {e.response.status_code} - {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Brave search failed: {e}")
            raise

    def __del__(self):
        """Close HTTP client on cleanup."""
        if hasattr(self, "client"):
            self.client.close()
