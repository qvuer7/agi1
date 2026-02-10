"""Extract reference product attributes from a reference product page."""

import json
import re
from bs4 import BeautifulSoup
from typing import Dict, Optional, List

from ..logging import get_logger

logger = get_logger(__name__)


def extract_reference_attributes(html: str, text: str, url: str) -> Dict[str, any]:
    """
    Extract reference product attributes from a product page.
    
    Used in Phase 1: Understand the reference product.
    
    Args:
        html: HTML content
        text: Extracted text content
        url: Page URL
        
    Returns:
        Dict with: {title, material, stones, brand, collection_keywords, price_range}
    """
    result: Dict[str, Optional[str]] = {
        "title": None,
        "material": None,
        "stones": None,
        "brand": None,
        "collection_keywords": [],
        "price_range": None,
    }
    
    if not html:
        return result
    
    try:
        soup = BeautifulSoup(html, "lxml")
        
        # 1. Extract title
        # Try JSON-LD first
        json_ld_scripts = soup.find_all("script", type="application/ld+json")
        for script in json_ld_scripts:
            try:
                data = json.loads(script.string)
                if isinstance(data, dict):
                    if data.get("@type") == "Product":
                        result["title"] = data.get("name") or data.get("title")
                        if "brand" in data:
                            brand_data = data["brand"]
                            if isinstance(brand_data, dict):
                                result["brand"] = brand_data.get("name")
                            elif isinstance(brand_data, str):
                                result["brand"] = brand_data
                        if "offers" in data and isinstance(data["offers"], dict):
                            price = data["offers"].get("price")
                            if price:
                                result["price_range"] = str(price)
                elif isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and item.get("@type") == "Product":
                            result["title"] = item.get("name") or item.get("title")
                            break
            except (json.JSONDecodeError, AttributeError, TypeError):
                continue
        
        # Fallback to meta tags or title tag
        if not result["title"]:
            og_title = soup.find("meta", property="og:title")
            if og_title:
                result["title"] = og_title.get("content", "").strip()
        
        if not result["title"]:
            title_tag = soup.find("title")
            if title_tag:
                result["title"] = title_tag.get_text(strip=True)
        
        # 2. Extract material and stones from text (heuristic)
        # Look for common jewelry/material keywords
        text_lower = text.lower()
        
        # Material keywords
        material_keywords = ["gold", "silver", "platinum", "steel", "titanium", "brass", "bronze"]
        for keyword in material_keywords:
            if keyword in text_lower:
                result["material"] = keyword
                break
        
        # Stone keywords
        stone_keywords = ["diamond", "ruby", "sapphire", "emerald", "pearl", "amber", "topaz", "amethyst"]
        found_stones = []
        for keyword in stone_keywords:
            if keyword in text_lower:
                found_stones.append(keyword)
        if found_stones:
            result["stones"] = ", ".join(found_stones)
        
        # 3. Extract brand (if not from JSON-LD)
        if not result["brand"]:
            # Try meta tags
            brand_meta = soup.find("meta", property="product:brand")
            if brand_meta:
                result["brand"] = brand_meta.get("content", "").strip()
        
        # 4. Extract collection keywords (from title or breadcrumbs)
        if result["title"]:
            # Simple heuristic: extract significant words from title
            title_words = re.findall(r'\b\w{4,}\b', result["title"].lower())
            # Filter out common words
            stop_words = {"product", "item", "buy", "shop", "store", "price", "sale"}
            result["collection_keywords"] = [w for w in title_words if w not in stop_words][:5]
        
        # 5. Extract price range
        if not result["price_range"]:
            # Look for price patterns in text
            price_patterns = [
                r'[\d,]+\.?\d*\s*(usd|eur|bgn|uah|rub|₴|€|\$|£)',
                r'price[:\s]+[\d,]+\.?\d*',
                r'[\d,]+\.?\d*\s*(грн|руб)',
            ]
            for pattern in price_patterns:
                match = re.search(pattern, text_lower)
                if match:
                    result["price_range"] = match.group(0)
                    break
        
    except Exception as e:
        logger.warning(f"Error extracting reference attributes: {e}")
    
    return result
