"""HTML to text extraction using trafilatura or BeautifulSoup fallback."""

import trafilatura
from bs4 import BeautifulSoup
from typing import Optional

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
