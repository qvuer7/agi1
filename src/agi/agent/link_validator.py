"""Link validation and verification for product pages."""

import re
from typing import Any, Dict, List
from urllib.parse import urlparse

from ..logging import get_logger

logger = get_logger(__name__)


def validate_product_page(
    expected_tokens: List[str],
    page_title: str,
    page_text: str,
    final_url: str,
) -> Dict[str, Any]:
    """
    Validate that a page matches expected product information.

    Args:
        expected_tokens: List of model strings/tokens to search for (e.g., ["OLED55C4", "LG C4"])
        page_title: Page title
        page_text: Extracted page text
        final_url: Final URL after redirects

    Returns:
        Dict with: {ok: bool, reason: str, score: float, final_url: str}
    """
    # Normalize inputs
    title_lower = (page_title or "").lower()
    text_lower = (page_text or "").lower()
    combined_text = f"{title_lower} {text_lower}"

    # Check if final_url is a generic redirect
    parsed = urlparse(final_url)
    generic_paths = ["/", "/search", "/home", "/index"]
    if parsed.path in generic_paths or "search" in parsed.path.lower():
        return {
            "ok": False,
            "reason": "URL redirects to generic homepage or search page",
            "score": 0.0,
            "final_url": final_url,
        }

    # Normalize expected tokens for matching
    normalized_tokens = []
    for token in expected_tokens:
        # Remove spaces, convert to lowercase
        normalized = re.sub(r"\s+", "", token.lower())
        normalized_tokens.append(normalized)
        # Also keep original for partial matching
        normalized_tokens.append(token.lower())

    # Check for matches
    matches = []
    for token in normalized_tokens:
        if token in combined_text:
            matches.append(token)
        # Also check for partial matches (e.g., "55C4" in "OLED55C4")
        if len(token) >= 4:
            for other_token in normalized_tokens:
                if token != other_token and token in other_token:
                    matches.append(token)

    if not matches:
        return {
            "ok": False,
            "reason": f"No matching model tokens found. Expected: {expected_tokens}",
            "score": 0.0,
            "final_url": final_url,
        }

    # Calculate match score (simple: number of unique matches / number of expected tokens)
    unique_matches = len(set(matches))
    expected_count = len(set(expected_tokens))
    score = min(1.0, unique_matches / max(1, expected_count))

    # Require at least one strong match
    if score < 0.3:  # At least 30% of expected tokens must match
        return {
            "ok": False,
            "reason": f"Weak match (score: {score:.2f}). Found: {set(matches)}",
            "score": score,
            "final_url": final_url,
        }

    return {
        "ok": True,
        "reason": f"Match found. Tokens: {set(matches)}",
        "score": score,
        "final_url": final_url,
    }


def is_generic_redirect(final_url: str) -> bool:
    return True
    # """Check if URL is a generic redirect."""
    # parsed = urlparse(final_url)
    # generic_paths = ["/", "/search", "/home", "/index"]
    # if parsed.path in generic_paths:
    #     return True
    # if "search" in parsed.path.lower() or "home" in parsed.path.lower():
    #     return True
    # return False


    
