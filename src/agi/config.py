"""Configuration management with environment variables."""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file
load_dotenv()

# OpenRouter configuration
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OR_MODEL = os.getenv("OR_MODEL", "moonshotai/kimi-k2.5")

# Brave Search configuration
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "")
BRAVE_BASE_URL = "https://api.search.brave.com/res/v1"

# HTTP client configuration
USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
HTTP_TIMEOUT = 30.0
HTTP_MAX_REDIRECTS = 10

# Browser (Playwright) configuration
BROWSER_HEADLESS = True
BROWSER_TIMEOUT = 20000  # milliseconds (reduced from 30s - use load state instead of networkidle)
BROWSER_NETWORK_IDLE_TIMEOUT = 3000  # milliseconds (reduced - optional wait)

# Cache configuration
CACHE_DIR = Path(os.getenv("CACHE_DIR", ".cache"))
CACHE_DIR.mkdir(exist_ok=True)

# Search cache TTL (seconds)
SEARCH_CACHE_TTL = 86400  # 1 day

# Fetch/render cache TTL (seconds)
FETCH_CACHE_TTL = 604800  # 7 days

# Agent configuration
DEFAULT_MAX_STEPS = 20
DEFAULT_MAX_PAGES_FETCHED = 8
MAX_PAGE_TEXT_LENGTH = 20000  # characters

# Link verification configuration
MAX_EXTRACTED_LINKS =  50  # Maximum links to extract per page
MIN_PRODUCT_LINKS_FOR_LISTING = 3  # Minimum product links to consider listing valid
