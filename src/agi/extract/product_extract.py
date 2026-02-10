"""Extract structured product data from HTML without LLM."""

import json
import re
from bs4 import BeautifulSoup
from typing import Dict, Optional

from ..logging import get_logger

logger = get_logger(__name__)


def extract_product_data(html: str) -> Dict[str, Optional[str]]:
    """
    Try to extract product data from HTML.

    Looks for:
    - JSON-LD @type: Product
    - Meta tags (og:title, og:price, etc.)
    - Common price patterns

    Args:
        html: HTML content

    Returns:
        Partial dict: {title, price, currency, sku, availability}
    """
    result: Dict[str, Optional[str]] = {
        "title": None,
        "price": None,
        "currency": None,
        "sku": None,
        "availability": None,
    }

    if not html:
        return result

    try:
        soup = BeautifulSoup(html, "lxml")

        # Try JSON-LD Product schema
        json_scripts = soup.find_all("script", type="application/ld+json")
        for script in json_scripts:
            try:
                data = json.loads(script.string)
                if isinstance(data, dict) and data.get("@type") == "Product":
                    result["title"] = data.get("name") or result["title"]
                    if "offers" in data:
                        offers = data["offers"]
                        if isinstance(offers, dict):
                            result["price"] = str(offers.get("price", "")) or result["price"]
                            result["currency"] = offers.get("priceCurrency") or result["currency"]
                            result["availability"] = offers.get("availability") or result["availability"]
                    result["sku"] = data.get("sku") or result["sku"]
                    break
            except (json.JSONDecodeError, AttributeError):
                continue

        # Try meta tags
        if not result["title"]:
            og_title = soup.find("meta", property="og:title")
            if og_title and og_title.get("content"):
                result["title"] = og_title["content"]

        if not result["price"]:
            og_price = soup.find("meta", property="product:price:amount")
            if og_price and og_price.get("content"):
                result["price"] = og_price["content"]
                og_currency = soup.find("meta", property="product:price:currency")
                if og_currency and og_currency.get("content"):
                    result["currency"] = og_currency["content"]

        # Try common price patterns in text
        if not result["price"]:
            price_patterns = [
                r'\$[\d,]+\.?\d*',  # $123.45
                r'[\d,]+\.?\d*\s*(USD|EUR|GBP)',  # 123.45 USD
                r'price[:\s]+[\d,]+\.?\d*',  # price: 123.45
            ]
            text = soup.get_text()
            for pattern in price_patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    result["price"] = match.group(0)
                    break

        # Clean up None values
        result = {k: v for k, v in result.items() if v is not None}

        if result:
            logger.debug(f"Extracted product data: {result}")

    except Exception as e:
        logger.warning(f"Product extraction failed: {e}")

    return result
