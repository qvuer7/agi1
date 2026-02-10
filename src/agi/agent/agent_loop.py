"""Agent loop for orchestrating tool calls and LLM interactions."""

from typing import Any, Dict, List, Optional, Set

from ..clients.openrouter_client import OpenRouterClient
from ..clients.brave_client import BraveClient
from ..clients.fetch_client import FetchClient
from ..clients.browser_client import BrowserClient
from ..extract.html_extract import extract_text
from ..extract.page_classifier import PageVerdict
from ..cache.cache import Cache
from ..agent.tool_schemas import TOOLS
from ..config import DEFAULT_MAX_STEPS, DEFAULT_MAX_PAGES_FETCHED
from ..logging import get_logger

logger = get_logger(__name__)


class AgentLoop:
    """Main agent loop that orchestrates tool calls and LLM interactions."""

    def __init__(
        self,
        openrouter_client: Optional[OpenRouterClient] = None,
        brave_client: Optional[BraveClient] = None,
        fetch_client: Optional[FetchClient] = None,
        browser_client: Optional[BrowserClient] = None,
        cache: Optional[Cache] = None,
    ):
        self.or_client = openrouter_client or OpenRouterClient()
        self.brave_client = brave_client or BraveClient()
        self.fetch_client = fetch_client or FetchClient()
        self.browser_client = browser_client or BrowserClient()
        self.cache = cache or Cache()

    def run(
        self,
        user_prompt: str,
        max_steps: int = DEFAULT_MAX_STEPS,
        max_pages_fetched: int = DEFAULT_MAX_PAGES_FETCHED,
    ) -> Dict[str, Any]:
        """
        Run the agent loop.

        Args:
            user_prompt: User's query/prompt
            max_steps: Maximum number of agent steps
            max_pages_fetched: Maximum number of pages to fetch

        Returns:
            Dict with: {answer, sources, debug}
        """
        messages: List[Dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "You are a product research assistant that finds similar products across websites.\n"
                    "You MUST always include the exact URLs you used as evidence for every extracted product.\n\n"

                    "WORKFLOW:\n\n"

                    "PHASE 1: Understand the reference product\n"
                    "- If user provides a reference product URL, fetch it with fetch_url (or render_url if needed)\n"
                    "- Extract reference attributes: title, material, stones, brand, collection keywords, price range\n"
                    "- If page is blocked/empty, try render_url then classify again\n\n"

                    "PHASE 2: Get entry point on target site\n"
                    "- Use search_web with 'site:domain.com keywords' format\n"
                    "- Build search query from reference attributes (brand, material, keywords)\n"
                    "- DO NOT invent URLs. Only use URLs returned by tools\n\n"

                    "PHASE 3: Extract product candidates from target site\n"
                    "- For each promising target URL from search results:\n"
                    "  * Use render_url for listing pages (often JavaScript-heavy)\n"
                    "  * Use ONLY the tool-returned product_candidate_links[] as candidate product URLs\n"
                    "  * If product_candidate_links is empty, discard the page (even if HTTP 200)\n\n"

                    "PHASE 4: Extract product details\n"
                    "- For product candidates:\n"
                    "  * fetch_url(product_url) for a fast check\n"
                    "  * If needed, use render_url(product_url) for JavaScript-heavy pages\n"
                    "  * Extract structured fields and compare with reference attributes\n"
                    "- Stop when you have enough products or budget exhausted\n\n"

                    "PHASE 5: Output\n"
                    "- Return products with: title, price (if available), product URL\n"
                    "- Include URLs that were returned by tools (search_web, fetch_url, render_url)\n"
                    "- Be clear and concise in your response\n\n"

                    "CRITICAL RULES:\n"
                    "- You may generate search queries (including site:domain format)\n"
                    "- Only use URLs returned by tools - do not invent or guess URLs\n"
                    "- **OUTPUT GATING**: You MUST output only URLs with page_type=PRODUCT\n"
                    "- Listing/category URLs (page_type=LISTING_WITH_PRODUCTS) are FORBIDDEN in final output\n"
                    "- If you have fewer than 5 PRODUCT URLs, continue searching for more candidates\n"
                    "- Never 'fill' the answer with listing URLs - only actual product pages are allowed\n"
                    "- Be concise but thorough. If search doesn't yield results, refine query and try again\n"
                ),
            },
            {"role": "user", "content": user_prompt},
        ]



        # URL tracking for sources
        fetched_urls: Set[str] = set()  # For limit tracking
        sources: List[Dict[str, str]] = []  # URLs used as sources
        debug_traces: List[Dict[str, Any]] = []
        
        # Provenance tracking: {url: {page_type, discovered_from, verified_by, ...}}
        url_provenance: Dict[str, Dict[str, Any]] = {}
        
        # Track verified PRODUCT URLs only
        verified_product_urls: Dict[str, Dict[str, Any]] = {}  # {url: {page_type, title, ...}}

        for step in range(max_steps):
            logger.info(f"Agent step {step + 1}/{max_steps}")

            # Call LLM
            try:
                assistant_message = self.or_client.chat(messages, tools=TOOLS)
            except Exception as e:
                logger.error(f"OpenRouter call failed: {e}")
                return {
                    "answer": f"Error communicating with LLM: {e}",
                    "sources": sources,
                    "debug": debug_traces,
                }

            # Check if we have a final answer
            if "tool_calls" not in assistant_message or not assistant_message["tool_calls"]:
                # Final answer
                answer = assistant_message.get("content", "")
                logger.info(f"Agent completed with final answer ({len(answer)} chars)")
                
                # Validate output: ensure all URLs in answer are PRODUCT type
                answer, validation_result = self._validate_output(answer, verified_product_urls)
                
                # If we don't have enough PRODUCT URLs, continue searching
                if len(verified_product_urls) < 5 and step < max_steps - 1:
                    logger.warning(f"Only {len(verified_product_urls)} PRODUCT URLs found, need 5. Continuing search...")
                    messages.append({
                        "role": "user",
                        "content": (
                            f"You provided an answer, but I need at least 5 URLs classified as PRODUCT pages. "
                            f"Currently I have {len(verified_product_urls)} PRODUCT URLs. "
                            f"Please continue searching for more product candidates from listing pages. "
                            f"Only URLs with page_type=PRODUCT can be included in the final answer. "
                            f"Listing/category URLs are not allowed."
                        ),
                    })
                    continue

                return {
                    "answer": answer,
                    "sources": [s for s in sources if s.get("page_type") == PageVerdict.PRODUCT],
                    "debug": debug_traces,
                    "validation": validation_result,
                }

            # Process tool calls
            messages.append(assistant_message)

            tool_calls = assistant_message.get("tool_calls", [])
            for tool_call in tool_calls:
                function_name = tool_call.get("function", {}).get("name", "")
                function_args_str = tool_call.get("function", {}).get("arguments", "{}")

                try:
                    import json
                    function_args = json.loads(function_args_str)
                except json.JSONDecodeError:
                    logger.error(f"Invalid tool call arguments: {function_args_str}")
                    continue

                logger.info(f"Tool call: {function_name}({function_args})")

                # Execute tool
                tool_result = self._execute_tool(
                    function_name,
                    function_args,
                    fetched_urls,
                    max_pages_fetched,
                )

                # Add tool result to messages
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.get("id"),
                    "content": tool_result["content"],
                })

                # Track provenance and verified PRODUCT URLs
                if "url" in tool_result and tool_result.get("success"):
                    url = tool_result.get("final_url") or tool_result.get("url")
                    page_type = tool_result.get("page_type", tool_result.get("verdict", "unknown"))
                    title = tool_result.get("title", url)
                    
                    # Update provenance
                    if url not in url_provenance:
                        url_provenance[url] = {}
                    
                    url_provenance[url].update({
                        "url": url,
                        "page_type": page_type,
                        "title": title,
                        "final_url": tool_result.get("final_url", url),
                        "canonical_url": tool_result.get("canonical_url"),
                    })
                    
                    # If this URL was discovered from a listing page, track that
                    if "discovered_from" in tool_result:
                        url_provenance[url]["discovered_from"] = tool_result["discovered_from"]
                    
                    # If this is a PRODUCT page, mark it as verified
                    if page_type == PageVerdict.PRODUCT:
                        url_provenance[url]["verified_by"] = function_name
                        verified_product_urls[url] = url_provenance[url].copy()
                        logger.info(f"Verified PRODUCT URL: {url}")
                    
                    # Add to sources if not already present (for all successful fetches)
                    if not any(s.get("url") == url for s in sources):
                        sources.append({
                            "url": url,
                            "title": title,
                            "page_type": page_type,
                        })

                # Track debug info
                debug_traces.append({
                    "step": step + 1,
                    "tool": function_name,
                    "args": function_args,
                    "result_length": len(tool_result.get("content", "")),
                    "success": tool_result.get("success", False),
                })

                # Check if we've hit the page limit
                if len(fetched_urls) >= max_pages_fetched:
                    logger.warning(f"Reached max_pages_fetched limit ({max_pages_fetched})")
                    messages.append({
                        "role": "user",
                        "content": f"Maximum number of pages fetched ({max_pages_fetched}). Please provide your final answer based on the information gathered so far.",
                    })

        # Max steps reached - return what we found
        logger.warning(f"Reached max_steps limit ({max_steps})")
        
        # Try to get a summary from the last assistant message if available
        final_answer = ""
        if messages:
            # Look for the last assistant message with content
            for msg in reversed(messages):
                if msg.get("role") == "assistant" and msg.get("content"):
                    final_answer = msg.get("content", "")
                    break
        
        # If no answer yet, generate a summary from what we found
        if not final_answer:
            if sources:
                final_answer = f"I found {len(sources)} source(s), but reached the step limit. "
                final_answer += "Here's what I gathered:\n\n"
                for source in sources:
                    final_answer += f"- {source.get('title', source['url'])}: {source['url']}\n"
            else:
                final_answer = "I reached the step limit before finding any sources. Please try a different query or increase max_steps."
        else:
            # Add a note that we hit the limit
            final_answer = f"[Note: Reached step limit] {final_answer}"

        return {
            "answer": final_answer,
            "sources": sources,
            "debug": debug_traces,
        }

    def _execute_tool(
        self,
        function_name: str,
        args: Dict[str, Any],
        fetched_urls: Set[str],
        max_pages_fetched: int,
    ) -> Dict[str, Any]:
        """Execute a tool call and return result."""
        if function_name == "search_web":
            query = args.get("query", "")
            count = args.get("count", 5)

            # Check cache
            cached = self.cache.get_search(query)
            if cached:
                results = cached
            else:
                try:
                    results = self.brave_client.search(query, count=count)
                    self.cache.set_search(query, results)
                except Exception as e:
                    logger.error(f"Search failed: {e}")
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

        elif function_name == "fetch_url":
            url = args.get("url", "")
            if not url:
                return {"content": "No URL provided", "success": False}

            if len(fetched_urls) >= max_pages_fetched:
                return {
                    "content": f"Maximum pages fetched ({max_pages_fetched}). Cannot fetch more.",
                    "success": False,
                }

            # Check cache
            cached = self.cache.get_fetch(url)
            if cached:
                fetch_result = cached
            else:
                fetch_result = self.fetch_client.fetch(url)
                if fetch_result.get("html"):
                    # Extract text
                    text = extract_text(fetch_result["html"])
                    fetch_result["text"] = text
                    self.cache.set_fetch(url, fetch_result)

            # Verify the page
            status = fetch_result.get("status", 0)
            final_url = fetch_result.get("final_url", url)
            
            # Auto-fallback to render_url if we get 403 (bot check) or 401 (unauthorized)
            if status == 403 or status == 401:
                logger.info(f"Got {status} for {url}, automatically falling back to render_url (likely bot check)")
                # Don't check cache for render - we want fresh attempt
                render_result = self.browser_client.render(url)
                if render_result.get("html") or render_result.get("text"):
                    # Extract text from HTML if we have it
                    if render_result.get("html") and not render_result.get("text"):
                        render_result["text"] = extract_text(render_result["html"])
                    # Cache the render result
                    self.cache.set_render(url, render_result)
                
                # Use render result instead of fetch result
                classification = render_result.get("classification", {})
                verdict_str = classification.get("verdict", "error")
                try:
                    verdict = PageVerdict(verdict_str) if isinstance(verdict_str, str) else verdict_str
                except (ValueError, TypeError):
                    verdict = PageVerdict.ERROR
                
                if not render_result.get("text") and not render_result.get("html"):
                    return {
                        "content": f"Failed to render {url} after 403 error. Page may be blocked or timeout occurred.",
                        "success": False,
                        "url": url,
                    }
                
                # Successfully rendered after 403
                fetched_urls.add(url)
                text = render_result.get("text", "")
                extracted_links = render_result.get("extracted_links", [])
                product_candidate_links = classification.get("product_candidate_links", [])
                final_url = render_result.get("final_url", url)
                canonical_url = render_result.get("canonical_url")
                
                content = f"Fetched {url} returned 403 (bot check), so rendered with browser instead:\n\n{text}"
                if product_candidate_links:
                    content += f"\n\nFound {len(product_candidate_links)} product candidate links:\n"
                    for i, link in enumerate(product_candidate_links[:10], 1):
                        content += f"{i}. {link}\n"
                    if len(product_candidate_links) > 10:
                        content += f"... and {len(product_candidate_links) - 10} more\n"
                
                return {
                    "content": content,
                    "success": True,
                    "url": final_url,
                    "final_url": final_url,
                    "canonical_url": canonical_url,
                    "extracted_links": extracted_links,
                    "product_candidate_links": product_candidate_links,
                    "page_type": verdict_str,  # Use page_type instead of verdict
                    "verdict": verdict_str,  # Keep for backward compatibility
                    "product_count": classification.get("product_count", 0),
                }
            
            # Normal flow for non-403 status codes
            if status != 200:
                error_msg = fetch_result.get("error", "Unknown error")
                return {
                    "content": f"Fetched {url} returned status {status} (error: {error_msg}). Try render_url if this is a JavaScript-heavy page.",
                    "success": False,
                    "url": final_url,
                    "status": status,
                    "error": error_msg,
                }

            fetched_urls.add(final_url)
            classification = fetch_result.get("classification", {})
            text = fetch_result.get("text", extract_text(fetch_result.get("html", "")))
            title = fetch_result.get("title", "")
            extracted_links = fetch_result.get("extracted_links", [])
            product_candidate_links = classification.get("product_candidate_links", [])
            canonical_url = fetch_result.get("canonical_url")
            verdict_str = classification.get("verdict", "error")
            
            # Extract SKU for product pages
            sku = None
            try:
                verdict = PageVerdict(verdict_str) if isinstance(verdict_str, str) else verdict_str
                if verdict == PageVerdict.PRODUCT:
                    from ..extract.sku_extract import extract_sku
                    sku = extract_sku(fetch_result.get("html", ""), text)
            except (ValueError, TypeError):
                pass
            
            # Format content to include product_candidate_links for listing pages
            content = f"Fetched {url}:\n\n{text}"
            if product_candidate_links:
                content += f"\n\nFound {len(product_candidate_links)} product candidate links:\n"
                for i, link in enumerate(product_candidate_links[:10], 1):  # Show first 10
                    content += f"{i}. {link}\n"
                if len(product_candidate_links) > 10:
                    content += f"... and {len(product_candidate_links) - 10} more\n"

            return {
                "content": content,
                "success": True,
                "url": final_url,
                "final_url": final_url,
                "canonical_url": canonical_url,
                "title": title,
                "sku": sku,
                "extracted_links": extracted_links,
                "product_candidate_links": product_candidate_links,
                "page_type": verdict_str,  # Use page_type instead of verdict
                "verdict": verdict_str,  # Keep for backward compatibility
                "product_count": classification.get("product_count", 0),
            }

        elif function_name == "render_url":
            url = args.get("url", "")
            if not url:
                return {"content": "No URL provided", "success": False}

            if len(fetched_urls) >= max_pages_fetched:
                return {
                    "content": f"Maximum pages fetched ({max_pages_fetched}). Cannot render more.",
                    "success": False,
                }

            # Check cache
            cached = self.cache.get_render(url)
            if cached:
                logger.info(f"Using cached render result for {url}")
                render_result = cached
            else:
                logger.info(f"Rendering {url} with Playwright (JS execution required for product links)")
                render_result = self.browser_client.render(url)
                if render_result.get("html") or render_result.get("text"):
                    # Extract text from HTML if we have it
                    if render_result.get("html") and not render_result.get("text"):
                        render_result["text"] = extract_text(render_result["html"])
                    self.cache.set_render(url, render_result)

            if not render_result.get("text") and not render_result.get("html"):
                return {
                    "content": f"Failed to render {url}. Page may be blocked or timeout occurred.",
                    "success": False,
                    "url": url,
                }

            fetched_urls.add(url)
            classification = render_result.get("classification", {})
            text = render_result.get("text", "")
            extracted_links = render_result.get("extracted_links", [])
            product_candidate_links = classification.get("product_candidate_links", [])
            final_url = render_result.get("final_url", url)
            canonical_url = render_result.get("canonical_url")
            verdict_str = classification.get("verdict", "error")
            
            # Extract SKU for product pages
            sku = None
            try:
                verdict = PageVerdict(verdict_str) if isinstance(verdict_str, str) else verdict_str
                if verdict == PageVerdict.PRODUCT:
                    from ..extract.sku_extract import extract_sku
                    sku = extract_sku(render_result.get("html", ""), text)
            except (ValueError, TypeError):
                pass
            
            # Format content to include product_candidate_links for listing pages
            content = f"Rendered {url}:\n\n{text}"
            if product_candidate_links:
                content += f"\n\nFound {len(product_candidate_links)} product candidate links:\n"
                for i, link in enumerate(product_candidate_links[:10], 1):  # Show first 10
                    content += f"{i}. {link}\n"
                if len(product_candidate_links) > 10:
                    content += f"... and {len(product_candidate_links) - 10} more\n"

            return {
                "content": content,
                "success": True,
                "url": final_url,
                "final_url": final_url,
                "canonical_url": canonical_url,
                "sku": sku,
                "extracted_links": extracted_links,
                "product_candidate_links": product_candidate_links,
                "page_type": verdict_str,  # Use page_type instead of verdict
                "verdict": verdict_str,  # Keep for backward compatibility
                "product_count": classification.get("product_count", 0),
            }

        else:
            return {
                "content": f"Unknown tool: {function_name}",
                "success": False,
            }
    
    def _validate_output(self, answer: str, verified_product_urls: Dict[str, Dict[str, Any]]) -> tuple[str, Dict[str, Any]]:
        """
        Validate that all URLs in the answer are PRODUCT type.
        
        Args:
            answer: LLM's final answer text
            verified_product_urls: Dict of verified PRODUCT URLs
            
        Returns:
            Tuple of (sanitized_answer, validation_result)
        """
        import re
        from urllib.parse import urlparse
        from ..extract.url_cleaner import clean_url
        
        verified_url_set = set(verified_product_urls.keys())
        found_urls = []
        rejected_urls = []
        
        # Find all URLs in the answer
        url_pattern = r'https?://[^\s<>"\'\)]+'
        urls_found = re.findall(url_pattern, answer)
        
        for url in urls_found:
            # Normalize URL
            normalized = clean_url(url.rstrip('.,;!?)'), remove_tracking=True)
            
            # Check if URL is a verified PRODUCT
            if normalized in verified_url_set:
                found_urls.append(normalized)
            else:
                # Try to find a verified PRODUCT URL on the same domain
                parsed = urlparse(normalized)
                domain = parsed.netloc
                replacement = None
                for verified_url in verified_url_set:
                    verified_parsed = urlparse(verified_url)
                    if verified_parsed.netloc == domain:
                        replacement = verified_url
                        break
                
                if replacement:
                    # Replace with verified PRODUCT URL
                    answer = answer.replace(url, replacement)
                    logger.info(f"Replaced non-PRODUCT URL {url} with verified PRODUCT URL {replacement}")
                    found_urls.append(replacement)
                else:
                    # Remove the URL - it's not a verified PRODUCT
                    answer = answer.replace(url, "[URL removed - not a verified PRODUCT page]")
                    rejected_urls.append(normalized)
                    logger.warning(f"Removed non-PRODUCT URL from output: {normalized}")
        
        validation_result = {
            "total_urls_found": len(urls_found),
            "verified_product_urls": len(found_urls),
            "rejected_urls": len(rejected_urls),
            "rejected_url_list": rejected_urls[:10],  # First 10 for debugging
        }
        
        return answer, validation_result
    