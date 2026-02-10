"""HTML to text extraction using trafilatura or BeautifulSoup fallback."""

import trafilatura
from bs4 import BeautifulSoup
from typing import List, Optional
from urllib.parse import urljoin, urlparse, urlunparse
from ..extract.url_cleaner import clean_url

from ..config import MAX_PAGE_TEXT_LENGTH
from ..logging import get_logger

logger = get_logger(__name__)


def extract_text(html: str, max_length: Optional[int] = None) -> str:
    """
    Extract readable text from HTML.

    Args:
        html: HTML content
        max_length: Maximum length to return (defaults to MAX_PAGE_TEXT_LENGTH)

    Returns:
        Extracted text, truncated to max_length
    """
    if not html:
        return ""

    max_length = max_length or MAX_PAGE_TEXT_LENGTH

    # Try trafilatura first (better quality)
    try:
        text = trafilatura.extract(html)
        if text:
            text = text.strip()
            if len(text) > max_length:
                text = text[:max_length] + "... [truncated]"
            logger.debug(f"Extracted {len(text)} chars using trafilatura")
            return text
    except Exception as e:
        logger.warning(f"Trafilatura extraction failed: {e}, falling back to BeautifulSoup")

    # Fallback to BeautifulSoup
    try:
        soup = BeautifulSoup(html, "lxml")
        # Remove script and style elements
        for script in soup(["script", "style"]):
            script.decompose()
        text = soup.get_text(separator=" ", strip=True)
        if len(text) > max_length:
            text = text[:max_length] + "... [truncated]"
        logger.debug(f"Extracted {len(text)} chars using BeautifulSoup")
        return text
    except Exception as e:
        logger.error(f"BeautifulSoup extraction failed: {e}")
        return ""


def extract_links(html: str, base_url: str, max_links: int = 50) -> List[str]:
    """
    Extract absolute URLs from HTML page.

    Args:
        html: HTML content
        base_url: Base URL for resolving relative links
        max_links: Maximum number of links to return

    Returns:
        List of absolute URLs, deduplicated and limited
    """
    if not html or not base_url:
        return []

    try:
        soup = BeautifulSoup(html, "lxml")
        links = set()

        # Extract from <a href>
        for anchor in soup.find_all("a", href=True):
            href = anchor.get("href", "").strip()
            if not href or href.startswith("#") or href.startswith("javascript:"):
                continue

            # Resolve to absolute URL
            absolute_url = urljoin(base_url, href)
            parsed = urlparse(absolute_url)

            # Only keep http/https URLs
            if parsed.scheme in ("http", "https"):
                # Normalize URL (remove fragment, clean tracking params, but keep important params)
                normalized = clean_url(absolute_url, remove_tracking=True)
                links.add(normalized)

        # Limit and return
        result = sorted(list(links))[:max_links]
        logger.debug(f"Extracted {len(result)} links from {base_url}")
        return result

    except Exception as e:
        logger.warning(f"Link extraction failed: {e}")
        return []
