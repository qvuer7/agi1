#!/usr/bin/env python3
"""
Standalone agent testing script with structured outputs and deterministic URL handling.
Pure Python script for testing agent logic without FastAPI.
"""

import sys
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from dataclasses import dataclass, field
from urllib.parse import urlparse, urljoin, urlunparse, urlencode
# Add src directory to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from agi.clients.openrouter_client import OpenRouterClient
from agi.clients.brave_client import BraveClient
from agi.clients.fetch_client import FetchClient
from agi.clients.browser_client import PlaywrightFetchClient
# Removed classify_page - relying fully on LLM for classification
from agi.logging import setup_logging, get_logger
import logging
from datetime import datetime

# Setup logging with file output
LOG_DIR = Path(".logs")
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / f"agent_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)

# Reduce httpx verbosity
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = get_logger(__name__)
logger.info(f"Logging to file: {LOG_FILE}")


# ============================================================================
# STATE MANAGEMENT
# ============================================================================

@dataclass
class AgentState:
    """Agent state for tracking progress - 3-phase workflow."""
    reference: Dict[str, Any] = field(default_factory=dict)  # reference_attributes from Phase 1
    target: Dict[str, Any] = field(default_factory=dict)  # domain, base_url
    site_plan: Dict[str, Any] = field(default_factory=dict)  # listing_urls from Phase 3
    helper_urls: Set[str] = field(default_factory=set)  # visited listing/category URLs
    visited: Set[str] = field(default_factory=set)  # all visited URLs
    allowed_urls: Set[str] = field(default_factory=set)  # URLs from tool outputs (for validation)
    brave_queries: List[str] = field(default_factory=list)  # Brave search queries generated
    brave_results: List[Dict[str, Any]] = field(default_factory=list)  # All Brave search results
    brave_url_set: Set[str] = field(default_factory=set)  # URLs from Brave results (for validation)


# ============================================================================
# JSON EXTRACTION HELPERS
# ============================================================================

def extract_first_json_object(s: str) -> Optional[Dict[str, Any]]:
    """
    Extract the first complete JSON object from a string.
    Uses brace counting to find complete objects.
    """
    if not s:
        return None
    
    # Find first opening brace
    start_idx = s.find("{")
    if start_idx == -1:
        return None
    
    # Scan forward counting braces
    brace_count = 0
    in_string = False
    escape_next = False
    
    for i in range(start_idx, len(s)):
        char = s[i]
        
        if escape_next:
            escape_next = False
            continue
        
        if char == "\\":
            escape_next = True
            continue
        
        if char == '"' and not escape_next:
            in_string = not in_string
            continue
        
        if in_string:
            continue
        
        if char == "{":
            brace_count += 1
        elif char == "}":
            brace_count -= 1
            if brace_count == 0:
                # Found complete object
                candidate = s[start_idx:i+1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    # Try next object
                    next_start = s.find("{", i + 1)
                    if next_start == -1:
                        return None
                    start_idx = next_start
                    brace_count = 0
                    continue
    
    return None


# ============================================================================
# PARSING HELPERS
# ============================================================================

def html_to_text_clean(html: str) -> str:
    """Clean HTML to text: remove script/style/noscript/svg/iframe/canvas/header/footer/nav; compress whitespace."""
    if not html:
        return ""
    
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        
        # Remove heavy/irrelevant tags
        for tag in soup(["script", "style", "noscript", "svg", "iframe", "canvas", "header", "footer", "nav"]):
            tag.decompose()
        
        # Remove HTML comments
        for comment in soup.find_all(string=lambda s: isinstance(s, type(soup.comment))):
            comment.extract()
        
        text = soup.get_text(separator=" ", strip=True)
        # Compress whitespace
        text = re.sub(r'\s+', ' ', text)
        return text.strip()
    except Exception as e:
        logger.warning(f"html_to_text_clean failed: {e}")
        return ""


def extract_links_rich(html: str, base_url: str) -> List[Dict[str, Any]]:
    """Extract links with anchor text and section classification."""
    if not html or not base_url:
        return []
    
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        links = []
        seen = set()
        
        for anchor in soup.find_all("a", href=True):
            href = anchor.get("href", "").strip()
            if not href or href.startswith("#") or href.startswith("javascript:"):
                continue
            
            # Resolve to absolute URL
            absolute_url = urljoin(base_url, href)
            parsed = urlparse(absolute_url)
            
            if parsed.scheme not in ("http", "https"):
                continue
            
            # Normalize URL
            normalized = normalize_url(absolute_url)
            if normalized in seen:
                continue
            seen.add(normalized)
            
            # Get anchor text
            anchor_text = anchor.get_text(strip=True)
            
            # Classify section (rough heuristic)
            section = "unknown"
            parent = anchor.parent
            for _ in range(5):  # Check up to 5 levels up
                if parent and parent.name:
                    parent_classes = " ".join(parent.get("class", [])).lower()
                    parent_id = (parent.get("id") or "").lower()
                    
                    if any(kw in parent_classes or kw in parent_id for kw in ["nav", "navigation", "menu"]):
                        section = "nav"
                        break
                    elif any(kw in parent_classes or kw in parent_id for kw in ["header", "top"]):
                        section = "header"
                        break
                    elif any(kw in parent_classes or kw in parent_id for kw in ["footer", "bottom"]):
                        section = "footer"
                        break
                    elif parent.name in ["nav", "header", "footer"]:
                        section = parent.name
                        break
                    elif parent.name == "main" or "main" in parent_classes:
                        section = "body"
                        break
                
                parent = getattr(parent, "parent", None)
            
            if section == "unknown" and anchor.find_parent("main"):
                section = "body"
            
            # Check if internal (same domain)
            base_domain = urlparse(base_url).netloc.lower()
            is_internal = parsed.netloc.lower() == base_domain or parsed.netloc == ""
            
            links.append({
                "url": normalized,
                "text": anchor_text[:200],  # Limit text length
                "is_internal": is_internal,
                "section": section,
            })
        
        return links[:200]  # Limit total links
    except Exception as e:
        logger.warning(f"extract_links_rich failed: {e}")
        return []


# ============================================================================
# URL GATING FUNCTIONS
# ============================================================================

def normalize_url(url: str) -> str:
    """Normalize URL: strip fragments, normalize trailing slash."""
    try:
        parsed = urlparse(url)
        # Remove fragment
        normalized = urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path.rstrip("/") or "/",  # Normalize trailing slash
            parsed.params,
            parsed.query,
            ""  # Remove fragment
        ))
        return normalized
    except Exception:
        return url


def is_same_domain(url: str, target_domain: str) -> bool:
    """Check if URL is on the same domain."""
    try:
        parsed = urlparse(url)
        url_domain = parsed.netloc.lower()
        target_domain_clean = target_domain.lower().replace("https://", "").replace("http://", "").split("/")[0]
        return url_domain == target_domain_clean or url_domain.endswith("." + target_domain_clean)
    except Exception:
        return False








# ============================================================================
# CONFIGURATION
# ============================================================================

SYSTEM_PROMPT = """You are a category/listing URL finder that identifies relevant search/filter/category pages on a target website using Brave search.

You MUST respond with valid JSON in one of these formats:

A) To call tools:
{
  "action": "CALL_TOOL",
  "tool_calls": [
    {"name": "search_web", "args": {"query": "...", "count": 10}},
    {"name": "render_url", "args": {"url": "..."}},
    {"name": "fetch_url", "args": {"url": "..."}}
  ],
  "notes": "explanation"
}

B) To provide structured data:
{
  "action": "PROVIDE_DATA",
  "reference_attributes": {...}  // Phase 1 only
  OR
  "candidate_queries": ["query1", "query2", ...]  // Phase 3A only: 3-6 Brave search queries
}

C) Final answer (Phase 3B):
{
  "action": "FINAL",
  "listing_urls": ["url1", "url2", ...],  // 3-8 listing/filter/category/search URLs
  "how_to_use": "instructions how to use these filters",
  "helper_urls": ["..."],  // optional: Brave URLs used as evidence
  "notes": "..."
}

CRITICAL RULES:
1. Never invent or guess URLs.
2. listing_urls MUST be listing/filter/category/search pages (NOT product/detail/ad pages).
3. listing_urls must be a subset of Brave search results provided to you.
4. Return exactly ONE JSON object each turn.
5. Return up to 5 listing URLs in FINAL (return as many as you can find).

WORKFLOW (3 PHASES):

PHASE 1: Reference Research
- render_url(reference_url) - always render
- Extract relevant product attributes based on product type (e.g., for cars: brand, model, year, fuel, drive, body; for jewelry: material, stones, brand, collection, style; etc.)
- Return reference_attributes JSON with attributes relevant to the product type

PHASE 2: Target Website Research (OPTIONAL, NARRATIVE ONLY)
- render_url(target_homepage) - always render
- Return plain text only. No JSON. No tool calls.
- Explain: "what are the entry points for search/filtering listings on this site"

PHASE 3A: Generate Brave Search Queries
- Based on reference_attributes + target domain, generate 3-6 Brave search queries
- Each query must include: site:TARGET_DOMAIN + brand + model + year + price + 1-2 extra attrs
- Return candidate_queries JSON

PHASE 3B: Filter Brave Results to Listing URLs
- You will receive Brave search results (url, title, snippet)
- Select up to 5 listing/category/search URLs from the results
- Prefer URLs with "search", "filter", "catalog", query params, category paths
- Return FINAL with listing_urls (subset of provided Brave results, up to 5)"""

# USER_PROMPT = """Based on this reference listing: https://auto.ria.com/auto_bmw_5_series_39462080.html
# Find max 5 listing/search/category URLs on https://auto.ria.com/ that would show similar cars.
# Do NOT return individual listing/detail URLs; only listing/search/filter pages."""
# USER_PROMPT = """Based on this reference listing: https://sovajewels.com/ua/p/koltso-iz-belogo-zolota-i-keramiki-smart-beautiful-artikul-110474820202/
# Find max 5 listing/search/category URLs on https://zolotiyvik.ua/ua/  that would show similar products.
# Do NOT return individual listing/detail URLs; only listing/search/filter pages."""

USER_PROMPT = """Based on this reference listing: https://sovajewels.com/p/tsepochka-iz-belogo-zolota-artikul-501111410201/
Find max 5 listing/search/category URLs on https://zolotiyvik.ua/ua/  that would show similar products.
Do NOT return individual listing/detail URLs; only listing/search/filter pages."""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Search the web for information. Use this first to find relevant URLs. For finding categories/listings on a specific site, use 'site:domain.com keywords' format.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query string. Use 'site:domain.com keywords' to search within a specific website."},
                    "count": {"type": "integer", "description": "Number of results to return (default: 10, max: 20)", "default": 10, "minimum": 1, "maximum": 20},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "Fetch and extract text content from a URL using HTTP GET (fast, no JavaScript). Use only for simple pages. For JavaScript-heavy pages or when you need links, use render_url instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to fetch"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "render_url",
            "description": "Render a URL using Playwright browser (executes JavaScript). Returns: text_excerpt (cleaned text), links_rich (all links with anchor text and context). Use this for: reference research, target site exploration, and listing/category pages.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to render"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "extract_search_schema",
            "description": "Extract search form schema from a page (form action, field names, select options). Use this to understand how to construct search URLs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL of page containing search form (usually homepage or search page)"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "build_url",
            "description": "Construct a URL by combining base_url with query parameters. Use this to build search/listing URLs from extract_search_schema results. The constructed URL is automatically added to allowed_urls.",
            "parameters": {
                "type": "object",
                "properties": {
                    "base_url": {"type": "string", "description": "Base URL (e.g., form action from extract_search_schema)"},
                    "params": {"type": "object", "description": "Query parameters as key-value pairs. Values can be strings or arrays of strings for repeated keys."},
                },
                "required": ["base_url", "params"],
            },
        },
    },
]

MAX_STEPS = 20
MAX_PAGES_FETCHED = 8
PLAYWRIGHT_HEADLESS = False
PLAYWRIGHT_USER_DATA_DIR = ".pw_test"
DEBUG_DIR = Path(".debug_agent")


# ============================================================================
# AGENT TESTER CLASS
# ============================================================================

class AgentTester:
    """Standalone agent tester with structured outputs and deterministic URL handling."""

    def __init__(
        self,
        system_prompt: str = SYSTEM_PROMPT,
        tools: Optional[List[Dict[str, Any]]] = None,
        max_steps: int = MAX_STEPS,
        playwright_headless: bool = PLAYWRIGHT_HEADLESS,
        playwright_user_data_dir: str = PLAYWRIGHT_USER_DATA_DIR,
    ):
        self.system_prompt = system_prompt
        self.tools = tools or TOOLS
        self.max_steps = max_steps
        self.debug_dir = DEBUG_DIR
        self.debug_dir.mkdir(exist_ok=True)

        # Initialize clients
        logger.info("Initializing clients...")
        try:
            self.or_client = OpenRouterClient()
            self.brave_client = BraveClient()
            self.fetch_client = FetchClient()
            self.playwright_client = PlaywrightFetchClient(
                headless=playwright_headless,
                user_data_dir=playwright_user_data_dir,
            )
            logger.info("All clients initialized successfully")
        except ValueError as e:
            logger.error(f"Failed to initialize clients: {e}")
            raise

    def run(self, user_prompt: str) -> Dict[str, Any]:
        """Run the 3-phase workflow."""
        # Initialize state
        state = AgentState()
        
        # Parse user prompt
        self._parse_user_prompt(user_prompt, state)
        
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        
        debug_traces: List[Dict[str, Any]] = []
        
        logger.info(f"Starting 3-phase workflow")
        logger.info(f"Reference URL: {state.reference.get('url', 'unknown')}")
        logger.info(f"Target domain: {state.target.get('domain', 'unknown')}")

        # ========================================================================
        # PHASE 1: Reference Research
        # ========================================================================
        logger.info("\n" + "="*80)
        logger.info("PHASE 1: Reference Research")
        logger.info("="*80)
        
        ref_url = state.reference.get("url")
        if not ref_url:
            return {
                "answer": "Error: Could not extract reference URL from prompt",
                "sources": [],
                "debug": debug_traces,
            }
        
        # Render reference page
        logger.info(f"Rendering reference page: {ref_url}")
        ref_result = self._execute_tool("render_url", {"url": ref_url}, state)
        
        if not ref_result.get("success"):
            return {
                "answer": f"Error: Failed to fetch reference page: {ref_result.get('content', 'Unknown error')}",
                "sources": [],
                "debug": debug_traces,
            }
        
        # Ask LLM for reference attributes
        ref_prompt = f"""Extract reference attributes from this product page. Identify the product type and extract relevant attributes.

Title: {ref_result.get('title', '')}
Text excerpt: {ref_result.get('text_excerpt', '')[:2000]}

Extract attributes that are relevant to this product type. For example:
- Cars: brand, model, year, fuel, drive, body, price_range, category
- Jewelry: material, stones, brand, collection, style, price_range, category
- Electronics: brand, model, specifications, price_range, category
- Clothing: brand, size, material, style, color, price_range, category

Return JSON with attributes relevant to this product:
{{
  "title": "...",
  "product_type": "...",  // e.g., "car", "jewelry", "electronics", "clothing"
  "category": "...",
  "price_range": "...",
  // Add other attributes relevant to this product type (brand, model, material, etc.)
  "key_attributes": ["...", "..."],
  "keywords": ["...", "..."]
}}"""
        
        messages.append({"role": "user", "content": ref_prompt})
        try:
            ref_response = self.or_client.chat(messages, tools=None)
            ref_content = ref_response.get("content", "")
            
            # Use robust JSON extractor
            ref_parsed = extract_first_json_object(ref_content)
            if ref_parsed:
                if "reference_attributes" in ref_parsed:
                    state.reference = ref_parsed["reference_attributes"]
                else:
                    state.reference = ref_parsed
                logger.info(f"Extracted reference attributes: {list(state.reference.keys())}")
            else:
                logger.warning("Could not parse reference attributes, using defaults")
                state.reference = {"url": ref_url}
        except Exception as e:
            logger.warning(f"Failed to extract reference attributes: {e}")
            state.reference = {"url": ref_url}
        
        # Always log Phase 1 results (regardless of success/failure)
        logger.info(f"\n{'='*80}")
        logger.info("PHASE 1 EXTRACTED DATA:")
        logger.info(f"{'='*80}")
        logger.info(json.dumps(state.reference, ensure_ascii=False, indent=2))
        logger.info(f"{'='*80}\n")

        # ========================================================================
        # PHASE 2: Target Website Research
        # ========================================================================
        logger.info("\n" + "="*80)
        logger.info("PHASE 2: Target Website Research")
        logger.info("="*80)
        
        target_url = state.target.get("base_url")
        if not target_url:
            return {
                "answer": "Error: Could not extract target website URL from prompt",
                "sources": [],
                "debug": debug_traces,
            }
        
        # Render target homepage
        logger.info(f"Rendering target homepage: {target_url}")
        target_result = self._execute_tool("render_url", {"url": target_url}, state)
        
        if not target_result.get("success"):
            return {
                "answer": f"Error: Failed to fetch target website: {target_result.get('content', 'Unknown error')}",
                "sources": [],
                "debug": debug_traces,
            }
        
        # Track allowed URLs from tool outputs
        links_rich = target_result.get("links_rich", [])
        for link in links_rich:
            url = link.get("url", "")
            if url:
                state.allowed_urls.add(normalize_url(url))
        
        # Limit links_rich to avoid context bloat - filter for category/listing/search related links
        category_keywords = ["search", "catalog", "category", "list", "filter", "browse", "find", "каталог", "поиск", "категория"]
        relevant_links = []
        for link in links_rich:
            text = link.get("text", "").lower()
            url = link.get("url", "").lower()
            if any(kw in text or kw in url for kw in category_keywords):
                relevant_links.append(link)
        
        # If we found relevant links, use them; otherwise use first 50
        links_to_show = relevant_links[:50] if relevant_links else links_rich[:50]
        
        # Ask LLM to explain site navigation (NARRATIVE ONLY - NO JSON, NO TOOL CALLS)
        site_prompt = f"""Analyze this target website and explain how to find category/listing/search pages.

Title: {target_result.get('title', '')}
Text excerpt: {target_result.get('text_excerpt', '')[:800]}

Available links ({len(links_rich)} total, showing {len(links_to_show)} most relevant):
{json.dumps(links_to_show, ensure_ascii=False, indent=2)}

Reference attributes:
{json.dumps(state.reference, ensure_ascii=False, indent=2)}

IMPORTANT: Return plain text only. No JSON. No tool calls. No multiple blocks.
Just describe: "What are the entry points for search/filtering/category pages on this site? What links should I click to find categories similar to the reference?"

Provide a brief explanation of the site's navigation structure in plain text."""
        
        messages.append({"role": "user", "content": site_prompt})
        try:
            site_response = self.or_client.chat(messages, tools=None)
            site_explanation = site_response.get("content", "")
            
            # Guard: Check if response contains JSON or tool calls
            if '"action"' in site_explanation or site_explanation.strip().startswith("{"):
                logger.warning("Phase 2 returned JSON/tool calls, re-asking with stricter wording")
                # Re-ask once with stricter wording
                strict_prompt = "Return ONLY plain text description. No JSON. No code blocks. No tool calls. Just describe the site navigation in natural language."
                messages.append({"role": "user", "content": strict_prompt})
                site_response = self.or_client.chat(messages, tools=None)
                site_explanation = site_response.get("content", "")
                
                # If still invalid, ignore Phase 2 (don't block Phase 3)
                if '"action"' in site_explanation or site_explanation.strip().startswith("{"):
                    logger.warning("Phase 2 still invalid after re-ask, ignoring it")
                    site_explanation = "Site navigation analysis skipped."
            
            logger.info(f"\n{'='*80}")
            logger.info("PHASE 2 EXTRACTED DATA (Site Navigation - Narrative):")
            logger.info(f"{'='*80}\n{site_explanation}\n")
            
            # Only append if it's plain text (not JSON)
            if not ('"action"' in site_explanation or site_explanation.strip().startswith("{")):
                messages.append({"role": "assistant", "content": site_explanation})
        except Exception as e:
            logger.warning(f"Failed to get site explanation: {e}")

        # ========================================================================
        # PHASE 3A: Generate Brave Search Queries
        # ========================================================================
        logger.info("\n" + "="*80)
        logger.info("PHASE 3A: Generate Brave Search Queries")
        logger.info("="*80)
        
        phase3a_prompt = f"""Based on the reference attributes, generate 3-6 Brave search queries to find listing/category/search pages on the target site.

Reference attributes:
{json.dumps(state.reference, ensure_ascii=False, indent=2)}

Target domain: {state.target.get('domain', '')}

You MUST generate 3-6 search queries. Each query must:
- Include: site:{state.target.get('domain', '')}
- Include: key attributes from reference_attributes (brand, model, material, category, etc.)
- Include: price or price_range if available
- Include: 1-2 additional relevant attributes from reference_attributes
- Be a search query string (NOT a URL)

Example formats:
- For cars: "site:auto.ria.com BMW 5 series 2016 xDrive 14990"
- For jewelry: "site:zolotiyvik.ua золото кольцо белое керамика"
- For electronics: "site:example.com iPhone 15 Pro 256GB"

Return JSON:
{{
  "action": "PROVIDE_DATA",
  "candidate_queries": ["query1", "query2", "query3", ...]  // 3-6 queries
}}"""
        
        messages.append({"role": "user", "content": phase3a_prompt})
        
        # Get queries from LLM
        try:
            phase3a_response = self.or_client.chat(messages, tools=None)
            phase3a_content = phase3a_response.get("content", "")
            
            parsed = extract_first_json_object(phase3a_content)
            if parsed and parsed.get("action") == "PROVIDE_DATA":
                candidate_queries = parsed.get("candidate_queries", [])
            else:
                candidate_queries = []
            
            if len(candidate_queries) < 3:
                logger.warning(f"Only {len(candidate_queries)} queries generated, using fallback")
                # Fallback: generate simple queries from available attributes
                domain = state.target.get("domain", "")
                # Collect available attributes (excluding metadata fields)
                attrs = []
                for key, value in state.reference.items():
                    if key not in ["url", "title", "key_attributes", "keywords", "product_type"] and value:
                        if isinstance(value, str) and value.strip():
                            attrs.append(value)
                        elif isinstance(value, list):
                            attrs.extend([str(v) for v in value if v])
                
                if attrs:
                    # Use first few attributes for queries
                    main_attrs = " ".join(attrs[:3])
                    candidate_queries = [
                        f"site:{domain} {main_attrs}",
                        f"site:{domain} {attrs[0] if attrs else 'search'}",
                        f"site:{domain} catalog",
                    ]
                else:
                    candidate_queries = [f"site:{domain} search", f"site:{domain} catalog"]
            
            state.brave_queries = candidate_queries[:6]  # Cap at 6
            logger.info(f"Generated {len(state.brave_queries)} Brave search queries")
            
        except Exception as e:
            logger.error(f"Phase 3A failed: {e}")
            return {
                "answer": f"Error: Failed to generate Brave search queries: {e}",
                "sources": self._get_sources_from_state(state),
                "debug": debug_traces,
            }
        
        # ========================================================================
        # Execute Brave Searches
        # ========================================================================
        logger.info("\n" + "="*80)
        logger.info("Executing Brave Searches")
        logger.info("="*80)
        
        for query in state.brave_queries:
            try:
                logger.info(f"Searching: {query}")
                results = self.brave_client.search(query, count=15)
                
                for result in results:
                    url = result.get("url", "")
                    if url:
                        normalized = normalize_url(url)
                        state.brave_url_set.add(normalized)
                        state.allowed_urls.add(normalized)
                        
                        # Store result with query info
                        state.brave_results.append({
                            "url": normalized,
                            "title": result.get("title", "")[:120],
                            "snippet": result.get("snippet", "")[:200],
                            "query": query,
                        })
                
                logger.info(f"  → Found {len(results)} results")
            except Exception as e:
                logger.warning(f"Brave search failed for '{query}': {e}")
        
        logger.info(f"Total Brave results: {len(state.brave_results)}")
        
        # ========================================================================
        # PHASE 3B: LLM Filters Brave Results to Listing URLs
        # ========================================================================
        logger.info("\n" + "="*80)
        logger.info("PHASE 3B: Filter Brave Results to Listing URLs")
        logger.info("="*80)
        
        # Limit results to 60 for context
        results_for_llm = state.brave_results[:60]
        
        max_iterations = 2
        validated_listing_urls = []
        
        for iteration in range(max_iterations):
            phase3b_prompt = f"""Select up to 5 listing/category/search URLs from these Brave search results.

Reference attributes:
{json.dumps(state.reference, ensure_ascii=False, indent=2)}

Target domain: {state.target.get('domain', '')}

Brave search results ({len(state.brave_results)} total, showing {len(results_for_llm)}):
{json.dumps(results_for_llm, ensure_ascii=False, indent=2)}

IMPORTANT:
- Return exactly one JSON object
- listing_urls MUST be a subset of the provided Brave results (exact URL match)
- listing_urls must be listing/category/search/filter pages (NOT detail/ad pages)
- Prefer URLs with: "search", "filter", "catalog", query params, category paths
- Return up to 5 listing URLs (return as many as you can find, even if less than 5)

Return JSON:
{{
  "action": "FINAL",
  "listing_urls": ["url1", "url2", "url3", ...],  // up to 5 URLs from Brave results
  "how_to_use": "instructions",
  "helper_urls": ["..."],  // optional: Brave URLs used as evidence
  "notes": "..."
}}"""
            
            if iteration > 0:
                messages.append({
                    "role": "user",
                    "content": f"Only {len(validated_listing_urls)} valid listing URLs found. Generate 2-3 new Brave queries focused on finding SEARCH/CATALOG pages, not ads. Then call search_web.",
                })
                # Allow LLM to generate new queries and search again
                try:
                    retry_response = self.or_client.chat(messages, tools=self.tools)
                    if "tool_calls" in retry_response and retry_response["tool_calls"]:
                        # Execute new searches
                        for tool_call in retry_response["tool_calls"]:
                            if tool_call.get("function", {}).get("name") == "search_web":
                                function_args_str = tool_call.get("function", {}).get("arguments", "{}")
                                try:
                                    function_args = json.loads(function_args_str) if isinstance(function_args_str, str) else {}
                                    query = function_args.get("query", "")
                                    count = function_args.get("count", 15)
                                    if query:
                                        results = self.brave_client.search(query, count=count)
                                        for result in results:
                                            url = result.get("url", "")
                                            if url:
                                                normalized = normalize_url(url)
                                                state.brave_url_set.add(normalized)
                                                state.allowed_urls.add(normalized)
                                                state.brave_results.append({
                                                    "url": normalized,
                                                    "title": result.get("title", "")[:120],
                                                    "snippet": result.get("snippet", "")[:200],
                                                    "query": query,
                                                })
                                except Exception as e:
                                    logger.warning(f"Failed to execute retry search: {e}")
                        # Update results_for_llm
                        results_for_llm = state.brave_results[:60]
                        continue
                except Exception:
                    pass
            
            messages.append({"role": "user", "content": phase3b_prompt})
            
            try:
                phase3b_response = self.or_client.chat(messages, tools=None)
                phase3b_content = phase3b_response.get("content", "")
                
                parsed = extract_first_json_object(phase3b_content)
                
                if parsed and parsed.get("action") == "FINAL":
                    candidate_listing_urls = parsed.get("listing_urls", [])
                else:
                    candidate_listing_urls = []
                
                # Validate URLs - rely fully on LLM for classification
                validated_urls = []
                for url in candidate_listing_urls[:5]:  # Cap at 5
                    normalized = normalize_url(url)
                    
                    # Check each validation condition separately for better logging
                    in_brave = normalized in state.brave_url_set
                    same_domain = is_same_domain(url, state.target.get("domain", ""))
                    
                    # Accept if: same domain (LLM is responsible for selecting listing URLs)
                    if same_domain:
                        if not in_brave:
                            logger.warning(f"URL not in Brave results but accepted (LLM selected it): {url}")
                        validated_urls.append(normalized)
                        state.helper_urls.add(normalized)
                    else:
                        logger.warning(f"Rejected URL: {url} (wrong domain)")
                
                validated_listing_urls = validated_urls
                
                # Accept any number of valid URLs (up to 5)
                if len(validated_listing_urls) > 0:
                    logger.info(f"Phase 3B complete: {len(validated_listing_urls)} valid listing URLs found")
                    break
                else:
                    logger.warning(f"No valid URLs found, iteration {iteration+1}/{max_iterations}")
                    
            except Exception as e:
                logger.error(f"Phase 3B failed: {e}")
                break
        
        if len(validated_listing_urls) == 0:
            return {
                "answer": f"Error: No valid listing URLs found.",
                "sources": self._get_sources_from_state(state),
                "debug": debug_traces,
            }
        
        # Final output
        final_output = {
            "action": "FINAL",
            "listing_urls": validated_listing_urls,
            "how_to_use": parsed.get("how_to_use", "") if parsed else "",
            "helper_urls": list(state.helper_urls),
            "notes": parsed.get("notes", f"Found {len(validated_listing_urls)} listing URLs from Brave search") if parsed else f"Found {len(validated_listing_urls)} listing URLs",
        }
        
        return {
            "answer": json.dumps(final_output, ensure_ascii=False, indent=2),
            "listing_urls": validated_listing_urls,
            "helper_urls": list(state.helper_urls),
            "sources": self._get_sources_from_state(state),
            "debug": debug_traces,
        }

    def _parse_user_prompt(self, user_prompt: str, state: AgentState):
        """Parse user prompt to extract reference URL and target domain."""
        # Extract URLs
        url_pattern = r'https?://[^\s<>"\'\)]+'
        urls = re.findall(url_pattern, user_prompt)
        
        # Strategy: if we have multiple URLs, the first detailed one is likely reference,
        # and a simple domain/homepage URL is likely target
        reference_url = None
        target_url = None
        
        # First pass: identify reference URLs (more specific criteria)
        for url in urls:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            path = parsed.path.rstrip("/")  # Remove trailing slash for consistent checking
            
            # Check if it's a reference URL (has detailed path content)
            # Strong indicators: numeric ID (6+ digits), .html extension, or very long path
            has_long_id = bool(re.search(r'\d{6,}', path))
            has_html = path.endswith('.html')
            path_segments = [s for s in path.split("/") if s]  # Filter empty segments
            has_long_path = len(path_segments) >= 3
            
            if has_long_id or has_html or (has_long_path and len(path) > 30):
                # This looks like a reference/product URL
                if not reference_url:  # Only set if not already set
                    reference_url = url
                    state.reference = {"url": url, "domain": domain}
        
        # Second pass: identify target URLs (simpler criteria)
        for url in urls:
            if url == reference_url:
                continue  # Skip if already identified as reference
            
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            path = parsed.path.rstrip("/")
            path_segments = [s for s in path.split("/") if s]
            
            # Check if it's a simple homepage/domain URL (likely target)
            if path == "" or path == "/" or len(path_segments) <= 2:
                # Simple path = likely target homepage/category page
                if not target_url:  # Only set if not already set
                    target_url = url
                    state.target = {
                        "domain": domain,
                        "base_url": url if path == "" or path == "/" else f"{parsed.scheme}://{domain}",
                    }
        
        # If we only found one URL, try to determine if it's reference or target
        if not reference_url and not target_url and len(urls) == 1:
            url = urls[0]
            parsed = urlparse(url)
            path = parsed.path.rstrip("/")
            path_segments = [s for s in path.split("/") if s]
            
            # If it has a long path, numeric ID, or looks detailed, assume it's reference
            has_long_id = bool(re.search(r'\d{6,}', path))
            has_html = path.endswith('.html')
            has_long_path = len(path_segments) >= 3 and len(path) > 30
            
            if has_long_id or has_html or has_long_path:
                state.reference = {"url": url, "domain": parsed.netloc.lower()}
                # Extract target domain from reference
                state.target = {
                    "domain": parsed.netloc.lower(),
                    "base_url": f"{parsed.scheme}://{parsed.netloc}",
                }
            else:
                state.target = {
                    "domain": parsed.netloc.lower(),
                    "base_url": url,
                }
        
        # If we found reference but no target, extract target from reference domain
        if reference_url and not target_url:
            parsed = urlparse(reference_url)
            state.target = {
                "domain": parsed.netloc.lower(),
                "base_url": f"{parsed.scheme}://{parsed.netloc}",
            }
        
        # If target not found, try to extract from text
        if not state.target:
            domain_match = re.search(r'сайт[е]?\s+(?:этой\s+)?компании[:\s]+(https?://[^\s]+)', user_prompt, re.I)
            if domain_match:
                target_url = domain_match.group(1)
                parsed = urlparse(target_url)
                state.target = {
                    "domain": parsed.netloc.lower(),
                    "base_url": target_url,
                }


    def _get_sources_from_state(self, state: AgentState) -> List[Dict[str, str]]:
        """Get sources list from state (visited URLs)."""
        sources = []
        seen = set()
        
        # Get all visited URLs as sources
        for url in state.visited:
            if url and url not in seen:
                seen.add(url)
                sources.append({
                    "url": url,
                    "title": url,  # Simple: just use URL as title
                })
        
        return sources


    def _execute_tool(self, function_name: str, args: Dict[str, Any], state: AgentState) -> Dict[str, Any]:
        """Execute a tool call and return structured result."""
        
        if function_name == "search_web":
            query = args.get("query", "")
            count = args.get("count", 5)

            try:
                results = self.brave_client.search(query, count=count)
            except Exception as e:
                logger.error(f"Search failed: {e}")
                return {
                    "success": False,
                    "query": query,
                    "error": str(e),
                    "content": f"Search failed: {e}",
                }

            if not results:
                return {
                    "success": False,
                    "query": query,
                    "results": [],
                    "content": "Search returned no results. Try a different query.",
                }

            # Format results with domain
            formatted_results = []
            for r in results:
                parsed = urlparse(r["url"])
                formatted_results.append({
                    "title": r["title"],
                    "url": r["url"],
                    "snippet": r["snippet"],
                    "domain": parsed.netloc.lower(),
                })

            return {
                "success": True,
                "query": query,
                "results": formatted_results,
                "content": f"Found {len(formatted_results)} search results. Check the 'results' array for URLs.",
            }

        elif function_name == "fetch_url":
            url = args.get("url", "")
            if not url:
                return {"success": False, "content": "No URL provided"}

            # Fetch the page
            fetch_result = self.fetch_client.fetch(url)
            
            status = fetch_result.get("status", 0)
            final_url = fetch_result.get("final_url", url)
            
            if status != 200:
                return {
                    "success": False,
                    "url": final_url,
                    "status": status,
                    "error": fetch_result.get("error", "Unknown error"),
                    "content": f"Fetched {url} returned status {status}. Try render_url if this is a JavaScript-heavy page.",
                }

            # Track visited
            normalized_url = normalize_url(final_url)
            if normalized_url not in state.visited:
                state.visited.add(normalized_url)
            
            # Process HTML
            html = fetch_result.get("html", "")
            text = html_to_text_clean(html)
            text_excerpt = text[:3000]  # Limit excerpt
            
            # Extract structured data - preserve links for catalogues
            links_rich = extract_links_rich(html, final_url)
            
            # Save full HTML to debug file
            debug_file = None
            if html:
                debug_file = self.debug_dir / f"fetch_{hash(final_url) % 1000000}.html"
                try:
                    debug_file.write_text(html, encoding="utf-8")
                except Exception:
                    pass

            return {
                "success": True,
                "url": final_url,
                "final_url": final_url,
                "status": status,
                "title": fetch_result.get("title", ""),
                "canonical_url": fetch_result.get("canonical_url"),
                "html": html,  # Include HTML for extraction
                "text_excerpt": text_excerpt,
                "links_rich": links_rich[:100],
                "content": f"Fetched {url}. Found {len(links_rich)} links. Check links_rich for navigation/category links.",
                "debug_file": str(debug_file) if debug_file else None,
            }

        elif function_name == "render_url":
            url = args.get("url", "")
            if not url:
                return {"success": False, "content": "No URL provided"}

            # Render with Playwright
            logger.info(f"Rendering {url} with Playwright...")
            render_result = self.playwright_client.fetch(url, wait_ms=1500, wait_until="load")
            
            if not render_result.get("html"):
                return {
                    "success": False,
                    "url": url,
                    "content": f"Failed to render {url}. Page may be blocked or timeout occurred.",
                }

            # Track visited
            final_url = render_result.get("final_url", url)
            normalized_url = normalize_url(final_url)
            if normalized_url not in state.visited:
                state.visited.add(normalized_url)
            
            # Process HTML
            html = render_result.get("html", "")
            title = render_result.get("title", "")
            text = html_to_text_clean(html)
            text_excerpt = text[:3000]  # Limit excerpt
            
            # Extract structured data - preserve links for catalogues
            links_rich = extract_links_rich(html, final_url)
            
            # Extract canonical URL
            canonical_url = None
            try:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html, "lxml")
                canonical_tag = soup.find("link", rel="canonical")
                if canonical_tag and canonical_tag.get("href"):
                    canonical_url = urljoin(final_url, canonical_tag["href"])
            except Exception:
                pass

            # Save full HTML to debug file
            debug_file = None
            if html:
                debug_file = self.debug_dir / f"render_{hash(final_url) % 1000000}.html"
                try:
                    debug_file.write_text(html, encoding="utf-8")
                except Exception:
                    pass

            return {
                "success": True,
                "url": final_url,
                "final_url": final_url,
                "status": render_result.get("status", 200),
                "title": title,
                "canonical_url": canonical_url,
                "html": html,  # Include HTML for extraction
                "text_excerpt": text_excerpt,
                "links_rich": links_rich[:100],
                "content": f"Rendered {url}. Found {len(links_rich)} links. Check links_rich for navigation/category links.",
                "debug_file": str(debug_file) if debug_file else None,
            }

        elif function_name == "extract_search_schema":
            url = args.get("url", "")
            if not url:
                return {"success": False, "content": "No URL provided"}
            
            # Render page to get HTML
            render_result = self.playwright_client.fetch(url, wait_ms=1000, wait_until="load")
            html = render_result.get("html", "")
            final_url = render_result.get("final_url", url)
            
            if not html:
                return {
                    "success": False,
                    "url": final_url,
                    "content": "Failed to fetch page HTML",
                }
            
            # Track visited
            state.visited.add(normalize_url(final_url))
            
            # Parse forms
            forms = []
            try:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html, "lxml")
                
                # Find all forms (max 3)
                all_forms = soup.find_all("form", limit=3)
                
                for form in all_forms:
                    # Get action URL
                    action = form.get("action", "")
                    if action:
                        action_url = urljoin(final_url, action)
                    else:
                        action_url = final_url
                    
                    method = form.get("method", "GET").upper()
                    
                    # Collect inputs
                    inputs_list = []
                    selects_list = []
                    hints = {}
                    
                    # Find all input/select/textarea with name
                    for input_elem in form.find_all(["input", "select", "textarea"]):
                        name = input_elem.get("name")
                        if not name:
                            continue
                        
                        input_type = input_elem.get("type", "").lower()
                        elem_type = input_elem.name
                        
                        if elem_type == "input":
                            inputs_list.append({
                                "name": name,
                                "type": input_type or "text",
                            })
                            
                            # Collect hints
                            placeholder = input_elem.get("placeholder", "")
                            aria_label = input_elem.get("aria-label", "")
                            if placeholder or aria_label:
                                hints[name] = placeholder or aria_label
                        
                        elif elem_type == "select":
                            options = []
                            for option in input_elem.find_all("option", limit=300):  # Cap at 300 total
                                value = option.get("value", "")
                                label = option.get_text(strip=True)
                                if value or label:
                                    options.append({
                                        "value": value,
                                        "label": label[:100],  # Limit label length
                                    })
                            
                            selects_list.append({
                                "name": name,
                                "options": options[:50],  # Cap at 50 per select
                            })
                        
                        elif elem_type == "textarea":
                            inputs_list.append({
                                "name": name,
                                "type": "textarea",
                            })
                    
                    # Try to find labels
                    for label in form.find_all("label"):
                        label_for = label.get("for")
                        label_text = label.get_text(strip=True)
                        if label_for and label_text:
                            hints[label_for] = label_text[:100]
                    
                    # Apply caps
                    if len(inputs_list) + len(selects_list) > 50:
                        # Truncate to keep most important
                        inputs_list = inputs_list[:30]
                        selects_list = selects_list[:20]
                    
                    forms.append({
                        "action": action_url,
                        "method": method,
                        "inputs": inputs_list,
                        "selects": selects_list,
                        "hints": hints,
                    })
                
                # Save schema to debug file
                schema_file = self.debug_dir / f"schema_{hash(final_url) % 1000000}.json"
                try:
                    schema_file.write_text(json.dumps({"forms": forms}, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception:
                    pass
                
                logger.info(f"Extracted search schema: {len(forms)} forms, {sum(len(f['inputs']) + len(f['selects']) for f in forms)} total fields")
                
                return {
                    "success": True,
                    "url": final_url,
                    "final_url": final_url,
                    "forms": forms,
                    "content": f"Extracted {len(forms)} search form(s) with {sum(len(f['inputs']) + len(f['selects']) for f in forms)} total fields. Check 'forms' array for schema.",
                }
            except Exception as e:
                logger.warning(f"extract_search_schema failed: {e}")
                return {
                    "success": False,
                    "url": final_url,
                    "content": f"Failed to extract schema: {e}",
                }
        
        elif function_name == "build_url":
            base_url = args.get("base_url", "")
            params = args.get("params", {})
            
            if not base_url:
                return {"success": False, "content": "No base_url provided"}
            
            try:
                # Build URL with params
                # Handle list values for repeated keys
                param_list = []
                for key, value in params.items():
                    if isinstance(value, list):
                        for v in value:
                            param_list.append((key, str(v)))
                    else:
                        param_list.append((key, str(value)))
                
                query_string = urlencode(param_list, doseq=False)
                
                # Combine base_url with query
                parsed_base = urlparse(base_url)
                if query_string:
                    if parsed_base.query:
                        final_query = f"{parsed_base.query}&{query_string}"
                    else:
                        final_query = query_string
                else:
                    final_query = parsed_base.query
                
                constructed_url = urlunparse((
                    parsed_base.scheme,
                    parsed_base.netloc,
                    parsed_base.path,
                    parsed_base.params,
                    final_query,
                    "",  # No fragment
                ))
                
                # Normalize
                normalized_url = normalize_url(constructed_url)
                
                # Add to allowed_urls (this is a tool output)
                state.allowed_urls.add(normalized_url)
                
                logger.info(f"Built URL: {normalized_url}")
                
                return {
                    "success": True,
                    "url": normalized_url,
                    "base_url": base_url,
                    "params": params,
                    "content": f"Built URL: {normalized_url}",
                }
            except Exception as e:
                logger.error(f"build_url failed: {e}")
                return {
                    "success": False,
                    "content": f"Failed to build URL: {e}",
                }
        
        else:
            return {
                "success": False,
                "content": f"Unknown tool: {function_name}",
            }

    def cleanup(self):
        """Clean up resources."""
        try:
            self.playwright_client.close()
            logger.info("Playwright client closed")
        except Exception as e:
            logger.warning(f"Error closing Playwright client: {e}")


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

def main():
    """Main entry point for the test script."""
    
    print("\n" + "="*80)
    print("AGENT TESTER - Standalone Testing Script (Structured Outputs)")
    print("="*80)
    print(f"\nSystem Prompt: {SYSTEM_PROMPT[:100]}...")
    print(f"User Prompt: {USER_PROMPT[:100]}...")
    print(f"Max Steps: {MAX_STEPS}")
    print(f"Tools: {len(TOOLS)}")
    print(f"\n📝 Logs saved to: {LOG_FILE}")
    print("\n" + "="*80 + "\n")

    # Initialize tester
    try:
        tester = AgentTester(
            system_prompt=SYSTEM_PROMPT,
            tools=TOOLS,
            max_steps=MAX_STEPS,
            playwright_headless=PLAYWRIGHT_HEADLESS,
            playwright_user_data_dir=PLAYWRIGHT_USER_DATA_DIR,
        )
    except ValueError as e:
        print(f"\n❌ Failed to initialize: {e}")
        return 1

    # Run agent
    try:
        result = tester.run(USER_PROMPT)
        
        # Display results
        print("\n" + "="*80)
        print("RESULTS")
        print("="*80)
        print("\nAnswer:")
        print(result.get("answer", ""))
        
        print("\n" + "-"*80)
        print("Listing URLs Found:")
        listing_urls = result.get("listing_urls", [])
        if listing_urls:
            for i, url in enumerate(listing_urls, 1):
                print(f"{i}. {url}")
        else:
            print("No listing URLs found.")
        
        if result.get("how_to_use"):
            print(f"\nHow to use: {result.get('how_to_use')}")
        
        print("\n" + "-"*80)
        print("Sources:")
        sources = result.get("sources", [])
        if sources:
            for i, source in enumerate(sources, 1):
                print(f"{i}. {source.get('title', 'N/A')}")
                print(f"   {source.get('url', 'N/A')}")
        else:
            print("No sources found.")
        
        print("\n" + "-"*80)
        print("Debug Info:")
        debug = result.get("debug", [])
        print(f"Total steps: {len(debug)}")
        for step in debug:
            print(f"  Step {step.get('step', '?')}: {step.get('tool', 'N/A')} - {'✓' if step.get('success') else '✗'}")
        
        print("\n" + "="*80)
        
        return 0
        
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user")
        return 1
    except Exception as e:
        logger.error(f"Error running agent: {e}", exc_info=True)
        print(f"\n❌ Error: {e}")
        return 1
    finally:
        tester.cleanup()


if __name__ == "__main__":
    sys.exit(main())
