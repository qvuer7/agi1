"""Disk-based cache for search results and fetched pages."""

import hashlib
from pathlib import Path
from typing import Any, Dict, Optional
import diskcache

from ..config import CACHE_DIR, SEARCH_CACHE_TTL, FETCH_CACHE_TTL
from ..logging import get_logger

logger = get_logger(__name__)


class Cache:
    """Disk cache for search results and fetched pages."""

    def __init__(self, cache_dir: Optional[Path] = None):
        cache_dir = cache_dir or CACHE_DIR
        self.cache = diskcache.Cache(str(cache_dir))

    def _make_key(self, prefix: str, identifier: str) -> str:
        """Create a cache key."""
        # Normalize identifier (e.g., URL) and create hash for long keys
        normalized = identifier.strip().lower()
        if len(normalized) > 100:
            normalized = hashlib.md5(normalized.encode()).hexdigest()
        return f"{prefix}:{normalized}"

    def get_search(self, query: str) -> Optional[list]:
        """Get cached search results."""
        key = self._make_key("search", query)
        result = self.cache.get(key)
        if result:
            logger.debug("Cache hit for search: %s", query)
        return result

    def set_search(self, query: str, results: list) -> None:
        """Cache search results."""
        key = self._make_key("search", query)
        self.cache.set(key, results, expire=SEARCH_CACHE_TTL)
        logger.debug("Cached search results for: %s", query)

    def get_fetch(self, url: str) -> Optional[Dict[str, Any]]:
        """Get cached fetch result."""
        key = self._make_key("fetch", url)
        result = self.cache.get(key)
        if result:
            logger.debug("Cache hit for fetch: %s", url)
        return result

    def set_fetch(self, url: str, data: Dict[str, Any]) -> None:
        """Cache fetch result."""
        key = self._make_key("fetch", url)
        self.cache.set(key, data, expire=FETCH_CACHE_TTL)
        logger.debug("Cached fetch result for: %s", url)

    def get_render(self, url: str) -> Optional[Dict[str, str]]:
        """Get cached render result."""
        key = self._make_key("render", url)
        result = self.cache.get(key)
        if result:
            logger.debug("Cache hit for render: %s", url)
        return result

    def set_render(self, url: str, data: Dict[str, str]) -> None:
        """Cache render result."""
        key = self._make_key("render", url)
        self.cache.set(key, data, expire=FETCH_CACHE_TTL)
        logger.debug("Cached render result for: %s", url)

    def clear(self) -> None:
        """Clear all cache."""
        self.cache.clear()
        logger.info("Cache cleared")
