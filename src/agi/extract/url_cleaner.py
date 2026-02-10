"""URL cleaning utilities - only remove tracking parameters."""

from urllib.parse import urlparse, urlunparse, parse_qs, urlencode


TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "fbclid", "msclkid", "twclid", "li_fat_id",
    "_ga", "_gid", "ref", "source", "affiliate_id",
}


def clean_url(url: str, remove_tracking: bool = True) -> str:
    """
    Clean URL by removing only tracking parameters.
    
    Args:
        url: URL to clean
        remove_tracking: If True, remove known tracking params (utm_*, gclid, etc.)
        
    Returns:
        Cleaned URL with tracking params removed (if enabled)
    """
    if not url:
        return url
    
    parsed = urlparse(url)
    
    if not remove_tracking or not parsed.query:
        # Just remove fragment
        return urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            parsed.query,
            "",  # Remove fragment only
        ))
    
    # Parse query params
    query_params = parse_qs(parsed.query, keep_blank_values=True)
    
    # Remove tracking params
    cleaned_params = {}
    for key, value in query_params.items():
        key_lower = key.lower()
        # Check if it's a tracking param
        is_tracking = (
            key_lower in TRACKING_PARAMS or
            key_lower.startswith("utm_") or
            key_lower.startswith("_ga") or
            key_lower in ("gclid", "fbclid", "msclkid", "twclid")
        )
        
        if not is_tracking:
            # Keep the param (preserve all values)
            cleaned_params[key] = value
    
    # Rebuild query string
    if cleaned_params:
        cleaned_query = urlencode(cleaned_params, doseq=True)
    else:
        cleaned_query = ""
    
    # Reconstruct URL
    return urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        parsed.params,
        cleaned_query,
        "",  # Remove fragment
    ))
