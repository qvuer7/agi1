"""Language-agnostic page classification for product/listing detection."""

import json
import re
from enum import Enum
from typing import Any, Dict, List
from urllib.parse import urlparse, urljoin, urlunparse
from ..extract.url_cleaner import clean_url

from bs4 import BeautifulSoup

from ..logging import get_logger

logger = get_logger(__name__)


class PageVerdict(str, Enum):
    """Page classification verdict."""
    PRODUCT = "product"
    LISTING_WITH_PRODUCTS = "listing_with_products"
    LISTING_EMPTY = "listing_empty"
    BLOCKED = "blocked"
    GENERIC = "generic"
    ERROR = "error"


def classify_page(html: str, text: str, url: str) -> Dict[str, Any]:
    """
    Classify a page using structural signals (language-agnostic).

    Args:
        html: HTML content
        text: Extracted text content
        url: Page URL

    Returns:
        Dict with: {verdict, product_count, reason, signals}
    """
    if not html:
        return {
            "verdict": PageVerdict.ERROR,
            "product_count": 0,
            "reason": "No HTML content",
            "signals": {},
        }

    parsed_url = urlparse(url)
    path_lower = parsed_url.path.lower()

    # Check for generic redirects
    generic_paths = ["/", "/home", "/index", "/search", "/category"]
    if any(path_lower == gp or path_lower.startswith(gp + "/") for gp in generic_paths):
        if not _has_product_signals(html, text):
            return {
                "verdict": PageVerdict.GENERIC,
                "product_count": 0,
                "reason": "Generic redirect page with no products",
                "signals": {"is_generic_path": True},
            }

    # Check for blocked/captcha pages
    blocked_signals = _detect_blocked(html, text)
    if blocked_signals["is_blocked"]:
        return {
            "verdict": PageVerdict.BLOCKED,
            "product_count": 0,
            "reason": blocked_signals["reason"],
            "signals": blocked_signals,
        }

    # Detect products using structural signals
    product_signals = _detect_products(html, text, url)
    product_count = product_signals["count"]

    # Check if URL looks like listing/search/category page
    is_listing_url = _is_listing_url(url)
    
    # Extract product candidate links for listing pages
    product_candidate_links = []
    if is_listing_url:
        product_candidate_links = _extract_product_candidate_links(html, url)

    if product_count > 0:
        if is_listing_url:
            return {
                "verdict": PageVerdict.LISTING_WITH_PRODUCTS,
                "product_count": product_count,
                "reason": f"Listing page with {product_count} products detected",
                "signals": product_signals,
                "product_candidate_links": product_candidate_links,
            }
        else:
            return {
                "verdict": PageVerdict.PRODUCT,
                "product_count": product_count,
                "reason": "Product page detected",
                "signals": product_signals,
                "product_candidate_links": [],  # Not a listing, no candidates
            }
    else:
        if is_listing_url:
            return {
                "verdict": PageVerdict.LISTING_EMPTY,
                "product_count": 0,
                "reason": "Listing/search page with no products",
                "signals": product_signals,
                "product_candidate_links": [],  # Empty listing, no candidates
            }
        else:
            # Could be product page with no schema, but we can't verify
            # Treat as potential product if it has some content
            if len(text) > 500:
                return {
                    "verdict": PageVerdict.PRODUCT,
                    "product_count": 1,  # Assume single product
                    "reason": "Potential product page (no schema but has content)",
                    "signals": product_signals,
                    "product_candidate_links": [],  # Not a listing
                }
            else:
                return {
                    "verdict": PageVerdict.ERROR,
                    "product_count": 0,
                    "reason": "Page has insufficient content",
                    "signals": product_signals,
                    "product_candidate_links": [],
                }


def _has_product_signals(html: str, text: str) -> bool:
    """Quick check if page has any product signals."""
    signals = _detect_products(html, text, "")
    return signals["count"] > 0


def _extract_product_candidate_links(html: str, base_url: str) -> List[str]:
    """
    Extract product candidate links from listing pages (DOM-based, not guessed).
    Uses strict allowlist patterns to ensure only product detail URLs are returned.
    
    Args:
        html: HTML content
        base_url: Base URL for resolving relative links
        
    Returns:
        List of absolute product candidate URLs that match strict allowlist patterns
    """
    candidates = []
    if not html:
        return candidates
    
    try:
        soup = BeautifulSoup(html, "lxml")
        parsed_base = urlparse(base_url)
        base_domain = parsed_base.netloc.lower()
        
        # STRICT ALLOWLIST: Site-specific product URL patterns
        # These patterns must match for a URL to be considered a product candidate
        strict_allowlist_patterns = [
            # auto.ria.com patterns
            r"/auto_[^/]+\.html$",  # /auto_bmw_5_series_39462080.html
            r"/auto_[^/]+/\d+\.html$",  # /auto_bmw/39462080.html
            
            # Generic e-commerce patterns (strict)
            r"/product/[^/]+/\d+",  # /product/item-name/12345
            r"/p/[^/]+",  # /p/product-slug
            r"/item/\d+",  # /item/12345
            r"/detail/\d+",  # /detail/12345
            r"/products/[^/]+",  # /products/product-slug
            
            # Pattern: URL ending with numeric ID (but not listing pages)
            r"/\d{6,}$",  # At least 6 digits (common for product IDs)
        ]
        
        # BLOCKLIST: Patterns that indicate listing/category pages (must NOT match)
        blocklist_patterns = [
            r"/search",
            r"/category",
            r"/catalog",
            r"/filter",
            r"/all",
            r"/list",
            r"/results",
            r"/car/[^/]+$",  # /car/bmw/5-series (listing, not product)
            r"/car/[^/]+/[^/]+$",  # /car/bmw/5-series/f10 (listing, not product)
        ]
        
        # Look for links in product containers
        product_containers = soup.find_all(
            ["div", "article", "li"],
            class_=re.compile(r"(product|item|card|goods)", re.I)
        )
        
        # Also check all links
        all_links = soup.find_all("a", href=True)
        
        seen_urls = set()
        
        for link in all_links:
            href = link.get("href", "").strip()
            if not href or href.startswith("#") or href.startswith("javascript:"):
                continue
            
            # Resolve to absolute URL
            absolute_url = urljoin(base_url, href)
            parsed = urlparse(absolute_url)
            
            # Only keep http/https URLs on same domain
            if parsed.scheme not in ("http", "https"):
                continue
            
            # Must be on same domain (no cross-domain links)
            if parsed.netloc.lower() != base_domain:
                continue
            
            path_lower = parsed.path.lower()
            
            # STRICT CHECK: Must match allowlist pattern
            matches_allowlist = any(re.search(pattern, path_lower) for pattern in strict_allowlist_patterns)
            
            # STRICT CHECK: Must NOT match blocklist patterns
            matches_blocklist = any(re.search(pattern, path_lower) for pattern in blocklist_patterns)
            
            if not matches_allowlist or matches_blocklist:
                continue
            
            # Additional validation: Check if link is in a product container (optional, but helps)
            in_product_container = False
            parent = link.parent
            for _ in range(3):  # Check up to 3 levels up
                if parent and parent.name:
                    parent_classes = " ".join(parent.get("class", [])).lower()
                    if any(keyword in parent_classes for keyword in ["product", "item", "card", "goods", "auto"]):
                        in_product_container = True
                        break
                parent = getattr(parent, "parent", None)
            
            # Normalize URL (remove fragment, clean tracking params)
            normalized = clean_url(absolute_url, remove_tracking=True)
            
            if normalized not in seen_urls:
                seen_urls.add(normalized)
                candidates.append(normalized)
        
        # Limit to reasonable number
        logger.debug(f"Extracted {len(candidates)} product candidate links (strict allowlist) from {base_url}")
        return candidates[:50]
    
    except Exception as e:
        logger.warning(f"Error extracting product candidate links: {e}")
        return []


def _detect_products(html: str, text: str, url: str) -> Dict[str, Any]:
    """
    Detect products using structural signals.

    Returns:
        Dict with: {count, json_ld_count, microdata_count, product_links_count, signals}
    """
    signals = {
        "count": 0,
        "json_ld_count": 0,
        "microdata_count": 0,
        "product_links_count": 0,
        "signals": [],
    }

    if not html:
        return signals

    try:
        soup = BeautifulSoup(html, "lxml")

        # 1. JSON-LD Product detection
        json_ld_scripts = soup.find_all("script", type="application/ld+json")
        for script in json_ld_scripts:
            try:
                data = json.loads(script.string)
                if isinstance(data, dict):
                    if data.get("@type") == "Product":
                        signals["json_ld_count"] += 1
                        signals["signals"].append("json_ld_product")
                    elif isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict) and item.get("@type") == "Product":
                                signals["json_ld_count"] += 1
                                signals["signals"].append("json_ld_product")
            except (json.JSONDecodeError, AttributeError, TypeError):
                continue

        # 2. Microdata Product detection
        microdata_products = soup.find_all(attrs={"itemtype": re.compile(r".*Product", re.I)})
        signals["microdata_count"] = len(microdata_products)
        if microdata_products:
            signals["signals"].append("microdata_product")

        # 3. Product-like link patterns (heuristic)
        # Look for links that might be product pages
        all_links = soup.find_all("a", href=True)
        product_link_patterns = [
            r"/product/",
            r"/p/",
            r"/item/",
            r"/detail/",
            r"/buy/",
            r"-\d+\.html",  # Common pattern: product-name-12345.html
            r"/\d+$",  # URL ending in numbers (common for product IDs)
        ]

        product_links = 0
        for link in all_links:
            href = link.get("href", "")
            if not href:
                continue

            # Check if link text or href suggests product
            link_text = link.get_text(strip=True).lower()
            href_lower = href.lower()

            # Pattern matching
            if any(re.search(pattern, href_lower) for pattern in product_link_patterns):
                product_links += 1
            # Check for product-like classes
            elif any(cls in link.get("class", []) for cls in ["product", "item", "card"]):
                product_links += 1
            # Check for price in link text (common pattern)
            elif re.search(r"[\d,]+\.?\d*\s*(usd|eur|bgn|uah|rub|₴|€|$|£)", link_text):
                product_links += 1

        signals["product_links_count"] = product_links
        if product_links > 0:
            signals["signals"].append("product_links")

        # Total count: prioritize schema, then links
        if signals["json_ld_count"] > 0 or signals["microdata_count"] > 0:
            signals["count"] = max(signals["json_ld_count"], signals["microdata_count"])
        elif product_links >= 3:  # Threshold: at least 3 product-like links
            signals["count"] = product_links
        elif product_links > 0:
            signals["count"] = 1  # At least one product link found

    except Exception as e:
        logger.warning(f"Error detecting products: {e}")

    return signals


def _is_listing_url(url: str) -> bool:
    """Check if URL looks like a listing/search/category page."""
    parsed = urlparse(url)
    path_lower = parsed.path.lower()
    query_lower = parsed.query.lower()

    listing_indicators = [
        "/search",
        "/category",
        "/catalog",
        "/c/",
        "/list",
        "/category/",
        "/shop/",
        "/products",
        "/items",
        "?q=",  # Search query
        "?search=",
        "?category=",
        "?filter=",
        "/l/",  # Common listing pattern
    ]

    return any(indicator in path_lower or indicator in query_lower for indicator in listing_indicators)


def _detect_blocked(html: str, text: str) -> Dict[str, Any]:
    """
    Detect blocked/captcha/interstitial pages using structural signals.

    Returns:
        Dict with: {is_blocked, reason, signals}
    """
    result = {
        "is_blocked": False,
        "reason": "",
        "signals": [],
    }

    if not html:
        return result

    try:
        soup = BeautifulSoup(html, "lxml")

        # Structural signals for blocked pages
        # 1. Very low content (likely interstitial)
        text_length = len(text.strip())
        if text_length < 200:
            # Check for common blocking elements
            blocking_elements = soup.find_all(
                ["form", "div"],
                class_=re.compile(r"(captcha|verify|block|access|gate|challenge)", re.I),
            )
            if blocking_elements:
                result["is_blocked"] = True
                result["reason"] = "Low content with blocking elements detected"
                result["signals"].append("blocking_elements")
                return result

        # 2. Captcha forms (structural detection)
        forms = soup.find_all("form")
        for form in forms:
            form_action = form.get("action", "").lower()
            form_id = form.get("id", "").lower()
            form_class = " ".join(form.get("class", [])).lower()

            captcha_keywords = ["captcha", "verify", "challenge", "recaptcha", "hcaptcha"]
            if any(keyword in form_action or keyword in form_id or keyword in form_class for keyword in captcha_keywords):
                result["is_blocked"] = True
                result["reason"] = "Captcha/verification form detected"
                result["signals"].append("captcha_form")
                return result

        # 3. Interstitial/access gate pages
        # Look for common patterns: "Please wait", "Checking your browser", etc.
        # But we avoid language-specific text, so we look for structural patterns instead
        meta_refresh = soup.find("meta", attrs={"http-equiv": re.compile(r"refresh", re.I)})
        if meta_refresh and text_length < 500:
            result["is_blocked"] = True
            result["reason"] = "Meta refresh with low content (likely interstitial)"
            result["signals"].append("meta_refresh")
            return result

        # 4. Very low content + specific structural patterns
        if text_length < 300:
            # Check if page has mostly scripts/iframes (common in blocking pages)
            scripts = len(soup.find_all("script"))
            iframes = len(soup.find_all("iframe"))
            if scripts > 5 and text_length < 200:
                result["is_blocked"] = True
                result["reason"] = "Low content with excessive scripts (likely blocking page)"
                result["signals"].append("low_content_high_scripts")
                return result

    except Exception as e:
        logger.warning(f"Error detecting blocked page: {e}")

    return result
