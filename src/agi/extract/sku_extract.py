"""Extract SKU/article number from product page."""

import json
import re
from bs4 import BeautifulSoup
from typing import Optional

from ..logging import get_logger

logger = get_logger(__name__)


def extract_sku(html: str, text: str) -> Optional[str]:
    """
    Extract SKU/article number from product page.
    
    Args:
        html: HTML content
        text: Extracted text content
        
    Returns:
        SKU/article number if found, None otherwise
    """
    if not html:
        return None
    
    try:
        soup = BeautifulSoup(html, "lxml")
        
        # 1. Try JSON-LD Product schema
        json_ld_scripts = soup.find_all("script", type="application/ld+json")
        for script in json_ld_scripts:
            try:
                data = json.loads(script.string)
                if isinstance(data, dict):
                    if data.get("@type") == "Product":
                        sku = data.get("sku") or data.get("mpn") or data.get("productID")
                        if sku:
                            return str(sku)
                elif isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and item.get("@type") == "Product":
                            sku = item.get("sku") or item.get("mpn") or item.get("productID")
                            if sku:
                                return str(sku)
            except (json.JSONDecodeError, AttributeError, TypeError):
                continue
        
        # 2. Try meta tags
        sku_meta = soup.find("meta", property="product:retailer_item_id")
        if sku_meta and sku_meta.get("content"):
            return sku_meta["content"].strip()
        
        # 3. Try microdata
        product_elem = soup.find(attrs={"itemtype": re.compile(r".*Product", re.I)})
        if product_elem:
            sku_elem = product_elem.find(attrs={"itemprop": "sku"})
            if sku_elem:
                return sku_elem.get_text(strip=True)
        
        # 4. Try common patterns in text
        text_lower = text.lower()
        sku_patterns = [
            r'sku[:\s]+([a-z0-9\-]+)',
            r'article[:\s]+([a-z0-9\-]+)',
            r'artikul[:\s]+([a-z0-9\-]+)',
            r'артикул[:\s]+([a-z0-9\-]+)',
            r'код[:\s]+([a-z0-9\-]+)',
        ]
        
        for pattern in sku_patterns:
            match = re.search(pattern, text_lower, re.IGNORECASE)
            if match:
                return match.group(1)
        
    except Exception as e:
        logger.debug(f"Error extracting SKU: {e}")
    
    return None
