# ============================================================
# Single-run notebook workflow (Phase 1 extract -> Phase 2 Brave)
# - Reads saved HTML (rendered externally)
# - Cleans to text
# - LLM extracts reference JSON
# - LLM plans Brave queries via tool_calls
# - Controller executes search_web tool calls
# - Feeds tool outputs back to LLM
# - LLM returns final JSON with candidate URLs
# ============================================================

from __future__ import annotations

from src.agi.config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, OR_MODEL, DEFAULT_MAX_PAGES_FETCHED
from src.agi.clients.openrouter_client import OpenRouterClient
from src.agi.clients.brave_client import BraveClient
from src.agi.clients.fetch_client import FetchClient
from src.agi.cache.cache import Cache
from src.agi.extract.html_extract import extract_text
from src.agi.clients.browser_client import PlaywrightFetchClient

# Final output construction
import json
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from typing import Any, Dict, List, Optional


client = OpenRouterClient()
brave_client = BraveClient()
fetch_client = FetchClient()
seed = PlaywrightFetchClient(headless=False, user_data_dir=".pw_sova")
cache = Cache()


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Search the web for information. Use this first to find relevant URLs. For finding products on a specific site, use 'site:domain.com keywords' format (e.g., 'site:rozetka.ua iPhone 15').",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query string. Use 'site:domain.com keywords' to search within a specific website.",
                    },
                    "count": {
                        "type": "integer",
                        "description": "Number of results to return (default: 5, max: 10)",
                        "default": 5,
                        "minimum": 1,
                        "maximum": 10,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "Fetch and extract text content from a URL. Use this after search_web to get page content. Use this to verify product pages.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL to fetch",
                    },
                },
                "required": ["url"],
            },
        },
    },
]



def execute_search_web(query: str, count: int = 5):
    """Execute search_web tool."""
    # Check cache
    cached = cache.get_search(query)
    if cached:
        results = cached
    else:
        try:
            results = brave_client.search(query, count=count)
            cache.set_search(query, results)
        except Exception as e:
            print(f"Search failed: {e}")
            return {
                "content": f"Search failed: {e}",
                "success": False,
            }
    
    if not results:
        return {
            "content": "Search returned no results. Try a different query.",
            "success": False,
        }
    
    # Format results
    formatted = "\n".join([
        f"- {r['title']}\n  URL: {r['url']}\n  {r['snippet']}"
        for r in results
    ])
    return {
        "content": f"Found {len(results)} search results:\n\n{formatted}",
        "success": True,
    }


def execute_fetch_url(url: str, fetched_urls: set, max_pages_fetched: int):
    """Execute fetch_url tool - basic HTTP fetch with status check."""
    if not url:
        return {"content": "No URL provided", "success": False}
    
    if len(fetched_urls) >= max_pages_fetched:
        return {
            "content": f"Maximum pages fetched ({max_pages_fetched}). Cannot fetch more.",
            "success": False,
        }
    
    # Check cache
    cached = cache.get_fetch(url)
    if cached:
        fetch_result = cached
    else:
        fetch_result = fetch_client.fetch(url)
        if fetch_result.get("html"):
            # Extract text
            text = extract_text(fetch_result["html"])
            fetch_result["text"] = text
            cache.set_fetch(url, fetch_result)
    
    status = fetch_result.get("status", 0)
    final_url = fetch_result.get("final_url", url)
    
    # Basic status check - only fail on 404 or other errors
    if status == 404:
        return {
            "content": f"Fetched {url} returned 404 (not found).",
            "success": False,
            "url": final_url,
            "status": status,
        }
    
    if status != 200:
        error_msg = fetch_result.get("error", "Unknown error")
        return {
            "content": f"Fetched {url} returned status {status} (error: {error_msg}).",
            "success": False,
            "url": final_url,
            "status": status,
            "error": error_msg,
        }
    
    # Success - page fetched
    fetched_urls.add(final_url)
    text = fetch_result.get("text", extract_text(fetch_result.get("html", "")))
    title = fetch_result.get("title", "")
    extracted_links = fetch_result.get("extracted_links", [])
    canonical_url = fetch_result.get("canonical_url")
    
    # Format content
    content = f"Fetched {url}:\n\n{text}"
    if extracted_links:
        content += f"\n\nFound {len(extracted_links)} links on this page:\n"
        for i, link in enumerate(extracted_links[:10], 1):  # Show first 10
            content += f"{i}. {link}\n"
        if len(extracted_links) > 10:
            content += f"... and {len(extracted_links) - 10} more\n"
    
    return {
        "content": content,
        "success": True,
        "url": final_url,
        "final_url": final_url,
        "canonical_url": canonical_url,
        "title": title,
        "extracted_links": extracted_links,
    }


def strip_html_basic(html: str) -> str:
    if not html:
        return ""

    soup = BeautifulSoup(html, "lxml")

    # remove heavy / irrelevant tags
    for tag in soup(["script", "style", "noscript", "svg", "iframe", "canvas"]):
        tag.decompose()

    # remove HTML comments
    for c in soup.find_all(string=lambda s: isinstance(s, type(soup.comment))):
        c.extract()

    return str(soup)


def html_to_text_basic(html: str, max_chars: int = 12000) -> str:
    cleaned = strip_html_basic(html)
    if not cleaned:
        return ""

    soup = BeautifulSoup(cleaned, "lxml")
    text = soup.get_text(separator="\n", strip=True)

    # collapse excessive newlines
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    text = "\n".join(lines)

    return text[:max_chars]


def extract_json_from_text(text: str) -> str:
    """
    Extract JSON from code-fence or raw braces.
    """
    # code fence
    m = re.search(r"```(?:json)?\s*({.*?})\s*```", text, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1)

    # raw object
    m = re.search(r"({.*})", text, re.DOTALL)
    if m:
        return m.group(1)

    return text


def parse_llm_json(content: str) -> dict:
    j = extract_json_from_text(content)
    return json.loads(j)


def run_tool_call(tool_call: Dict[str, Any]) -> Dict[str, Any]:
    fn = tool_call.get("function", {})
    name = fn.get("name", "")
    args_str = fn.get("arguments", "{}")
    try:
        args = json.loads(args_str) if isinstance(args_str, str) else (args_str or {})
    except Exception:
        args = {}

    if name == "search_web":
        query = args.get("query", "")
        count = int(args.get("count", 5) or 5)
        return execute_search_web(query=query, count=count)

    return {"success": False, "content": f"Unknown tool: {name}", "name": name}



def agent_chat_with_tools(
    client,
    messages: List[Dict[str, Any]],
    tools: Optional[list] = None,
    max_tool_rounds: int = 6,
) -> Dict[str, Any]:
    """
    Runs the LLM, executes requested tool calls, feeds results back,
    and repeats until no tool calls remain or max_tool_rounds reached.

    Returns last assistant response dict.
    """
    rounds = 0
    last = None

    while True:
        if tools is None:
            out = client.chat(messages)
        else:
            out = client.chat(messages, tools=tools)

        last = out

        # Some clients return assistant message separately; normalize:
        assistant_msg = {
            "role": "assistant",
            "content": out.get("content", "") or "",
        }

        # Preserve tool_calls if present (so we can attach them to messages)
        tool_calls = out.get("tool_calls") or []
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls

        messages.append(assistant_msg)

        if not tool_calls:
            return out

        rounds += 1
        if rounds > max_tool_rounds:
            return out  # stop to avoid infinite loops

        # Execute tool calls and append tool messages
        for tc in tool_calls:
            tool_result = run_tool_call(tc)

            # IMPORTANT:
            # Feed structured JSON to the model (not only the formatted string).
            tool_content = json.dumps(tool_result, ensure_ascii=False)

            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id"),
                "content": tool_content,
            })






with open(r"C:\agi-1\debug_sova_seed.html", "r", encoding="utf-8") as f:
    html = f.read()

textbasic = html_to_text_basic(html, max_chars=12000)

phase1_system = (
    "You are a product information extractor.\n"
    "You receive plain text extracted from an HTML product page.\n"
    "Your task is to identify ONE main product and extract its key information.\n\n"
    "OUTPUT RULES:\n"
    "- Return ONLY valid JSON.\n"
    "- Do NOT add explanations.\n"
    "- Do NOT add markdown.\n"
    "- Do NOT invent information.\n"
    "- If some field is not present, return an empty string or null.\n\n"
    "REQUIRED JSON FORMAT:\n"
    "{\n"
    '  \"title\": \"\",\n'
    '  \"price\": null,\n'
    '  \"currency\": \"\",\n'
    '  \"characteristics\": [\n'
    '    \"\",\n'
    '    \"\",\n'
    '    \"\"\n'
    "  ]\n"
    "}\n\n"
    "FIELD DEFINITIONS:\n"
    "- title: main product name as shown in the text\n"
    "- price: numeric value of the main product price (no currency symbols)\n"
    "- currency: currency code or symbol (e.g. UAH, USD, EUR, ₴, $, €)\n"
    "- characteristics: 3 to 6 most important technical or descriptive attributes "
    "(material, size, model, color, features, compatibility, etc.)\n"
)

messages_phase1 = [
    {"role": "system", "content": phase1_system},
    {"role": "user", "content": textbasic},
]

out1 = client.chat(messages_phase1)
ref_json = parse_llm_json(out1["content"])

print("PHASE1 ref_json:", json.dumps(ref_json, ensure_ascii=False, indent=2))


# ============================================================
# Phase 2: Brave discovery (LLM calls search_web)
# ============================================================

target_domain = "zolotiyvik.ua"

phase2_system = (
    "You are a product search agent.\n"
    "Input: JSON with (target_domain, reference).\n"
    "Tools: search_web (Brave).\n\n"
    "TASK:\n"
    "Using ONLY search_web, find candidate URLs on target_domain that are likely to contain products similar to the reference.\n"
    "Your output for now is discovery only (no page fetching).\n\n"
    "RULES:\n"
    "- Use ONLY URLs returned by search_web.\n"
    "- Always restrict queries to target_domain using: site:TARGET_DOMAIN\n"
    "- Do NOT assume any domain-specific vocabulary.\n"
    "- Generate queries using the reference fields:\n"
    "  - title\n"
    "  - characteristics (as free-text tokens)\n"
    "  - price + currency (use as optional constraints)\n"
    "- Prefer broader queries first, then narrower.\n"
    "- Run 6 to 10 search_web calls maximum.\n"
    "- Collect and deduplicate URLs.\n"
    "- Guess url_type from snippet+path only: one of [\"listing\", \"product\", \"other\", \"unknown\"].\n\n"
    "QUERY STRATEGY:\n"
    "1) Start broad: site:TARGET_DOMAIN + short title keywords (2-4 words)\n"
    "2) Add 1-2 strongest characteristics (keep them as-is; do not normalize)\n"
    "3) If results are too broad, add price as a hint (optional)\n"
    "4) Try language variants only by removing/adding punctuation and using substrings, not translations.\n\n"
    "OUTPUT (RETURN ONLY VALID JSON):\n"
    "{\n"
    "  \"target_domain\": \"\",\n"
    "  \"queries_used\": [\"...\"],\n"
    "  \"candidates\": [\n"
    "    {\n"
    "      \"url\": \"\",\n"
    "      \"title\": \"\",\n"
    "      \"snippet\": \"\",\n"
    "      \"url_type_guess\": \"listing|product|other|unknown\",\n"
    "      \"matched_reference_bits\": [\"...\"],\n"
    "      \"evidence_query\": \"...\"\n"
    "    }\n"
    "  ]\n"
    "}\n"
)

phase2_input = json.dumps(
    {"target_domain": target_domain, "reference": ref_json},
    ensure_ascii=False
)

messages_phase2 = [
    {"role": "system", "content": phase2_system},
    {"role": "user", "content": phase2_input},
]

# Run tool-agent loop (LLM -> tool calls -> results -> LLM -> final JSON)
out2 = agent_chat_with_tools(
    client=client,
    messages=messages_phase2,
    tools=TOOLS,               # must include search_web schema
    max_tool_rounds=6,
)

# The final model message should be JSON (content field)
print("PHASE2 raw content:\n", out2.get("content", "")[:2000])

phase2_json = parse_llm_json(out2.get("content", "{}"))
print("PHASE2 parsed:\n", json.dumps(phase2_json, ensure_ascii=False, indent=2))


# ============================================================
# Optional: extract listing URLs only (quick view)
# ============================================================
listing_urls = [
    c["url"]
    for c in phase2_json.get("candidates", [])
    if c.get("url_type_guess") == "listing" and c.get("url")
]

print("\nListing candidates:")
for u in listing_urls[:20]:
    print("-", u)
