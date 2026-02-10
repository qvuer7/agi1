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
                    "You are a product research assistant that finds similar products across websites. "
                    "Follow this workflow:\n\n"
                    
                    "PHASE 1: Understand the reference product\n"
                    "- If user provides a reference product URL, fetch it with fetch_url (or render_url if needed)\n"
                    "- Extract reference attributes: title, material, stones, brand, collection keywords, price range\n"
                    "- If page is blocked/empty, try render_url then classify again\n\n"
                    
                    "PHASE 2: Get entry point on target site\n"
                    "- Use search_web with 'site:domain.com keywords' format \n"
                    "- Build search query from reference attributes (brand, material, keywords)\n"
                    "- DO NOT parse target site into text - use site:domain searches\n\n"
                    
                    "PHASE 3: Extract product candidates from target site\n"
                    "- For each promising target URL from search results:\n"
                    "  * Use render_url for listing pages (they're often JavaScript-heavy)\n"
                    "  * The tool returns product_candidate_links[] extracted from DOM\n"
                    "  * If product_candidate_links is empty, discard the page (even if HTTP 200)\n\n"
                    
                    "PHASE 4: Verify product pages\n"
                    "- For first K candidates (up to 15):\n"
                    "  * fetch_url(product_url) - fast check\n"
                    "  * If not PRODUCT verdict, try render_url and classify again\n"
                    "  * If still not PRODUCT, discard\n"
                    "  * Extract structured fields and compare with reference attributes\n"
                    "- Stop when you have enough verified products or budget exhausted\n\n"
                    
                    "PHASE 5: Output\n"
                    "- Return verified products with: title, price (if available), verified product URL\n"
                    "- Only URLs that were successfully fetched/rendered can appear in output\n\n"
                    
                    "CRITICAL RULES:\n"
                    "- LLM may generate search queries (including site:domain format)\n"
                    "- LLM may NOT generate product URLs - only use URLs returned by tools\n"
                    "- Never hallucinate or guess URL structures\n"
                    "- All product URLs must be verified via fetch_url/render_url\n"
                    "- If a listing page has no product_candidate_links, it's useless - discard it\n"
                    "- Be concise but thorough. If search doesn't yield results, refine query and try again."
                ),
            },
            {"role": "user", "content": user_prompt},
        ]


        # Provenance tracking
        attempted_urls: Set[str] = set()  # All URLs we tried to fetch
        verified_urls: Dict[str, Dict[str, Any]] = {}  # {url: {verdict, product_count, reason, ...}}
        rejected_urls: Dict[str, str] = {}  # {url: reason}
        sources: List[Dict[str, str]] = []  # Only verified URLs for final response
        fetched_urls: Set[str] = set()  # For limit tracking
        debug_traces: List[Dict[str, Any]] = []

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

                # Final URL correctness gate - verify all product URLs before returning
                verified_urls = self._verify_product_urls(verified_urls, fetched_urls, max_pages_fetched)

                # Sanitize output so the answer includes only verified links
                answer = self._sanitize_output(answer, verified_urls)

                return {
                    "answer": answer,
                    "sources": self._get_verified_sources_only(verified_urls),
                    "debug": debug_traces,
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
                    attempted_urls,
                    verified_urls,
                    rejected_urls,
                )

                # Add tool result to messages
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.get("id"),
                    "content": tool_result["content"],
                })

                # Track provenance and sources
                if "url" in tool_result:
                    url = tool_result["url"]
                    attempted_urls.add(url)
                    
                    # Get URL metadata
                    final_url = tool_result.get("final_url", url)
                    canonical_url = tool_result.get("canonical_url")
                    
                    # Check if URL is verified (good)
                    if tool_result.get("is_verified"):
                        verified_urls[url] = {
                            "url": url,
                            "title": tool_result.get("title", url),
                            "verdict": tool_result.get("verdict"),
                            "product_count": tool_result.get("product_count", 0),
                            "reason": tool_result.get("verification_reason", ""),
                            "final_url": final_url,
                            "canonical_url": canonical_url,
                            "sku": tool_result.get("sku"),
                        }
                    elif tool_result.get("rejection_reason"):
                        rejected_urls[url] = tool_result.get("rejection_reason")

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
            verified_sources = self._get_verified_sources_only(verified_urls)
            if verified_sources:
                final_answer = f"I found {len(verified_sources)} verified source(s), but reached the step limit. "
                final_answer += "Here's what I gathered:\n\n"
                for source in verified_sources:
                    final_answer += f"- {source.get('title', source['url'])}: {source['url']}\n"
            else:
                final_answer = "I reached the step limit before finding any verified sources. Please try a different query or increase max_steps."
        else:
            # Add a note that we hit the limit
            final_answer = f"[Note: Reached step limit] {final_answer}"
        
        # Sanitize output to remove unverified URLs
        final_answer = self._sanitize_output(final_answer, verified_urls)
        
        # Final URL correctness gate - verify all product URLs before returning
        verified_urls = self._verify_product_urls(verified_urls, fetched_urls, max_pages_fetched)

        return {
            "answer": final_answer,
            "sources": self._get_verified_sources_only(verified_urls),
            "debug": debug_traces,
        }

    def _execute_tool(
        self,
        function_name: str,
        args: Dict[str, Any],
        fetched_urls: Set[str],
        max_pages_fetched: int,
        attempted_urls: Set[str],
        verified_urls: Dict[str, Dict[str, Any]],
        rejected_urls: Dict[str, str],
    ) -> Dict[str, Any]:
        """Execute a tool call and return result with verification."""
        # Note: attempted_urls, verified_urls, rejected_urls are tracked in the caller
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
                
                # Check if page is good (verified)
                is_verified = self._is_good_url(200, url, verdict, classification)  # Assume 200 for render
                
                if not render_result.get("text") and not render_result.get("html"):
                    return {
                        "content": f"Failed to render {url} after 403 error. Page may be blocked or timeout occurred.",
                        "success": False,
                        "url": url,
                        "is_verified": False,
                        "rejection_reason": "Failed to render after 403",
                    }
                
                if not is_verified:
                    rejection_reason = classification.get("reason", "Page did not pass verification")
                    if verdict == PageVerdict.LISTING_EMPTY:
                        rejection_reason = "Empty listing page (no products found)"
                    elif verdict == PageVerdict.BLOCKED:
                        rejection_reason = "Page is blocked or requires verification"
                    elif verdict == PageVerdict.GENERIC:
                        rejection_reason = "Generic redirect page"
                    elif verdict == PageVerdict.ERROR:
                        rejection_reason = "Page classification error"
                    
                    text = render_result.get("text", "")
                    
                    return {
                        "content": f"Rendered {url} (after 403) but page is not valid: {rejection_reason}.\n\nPage content:\n{text[:500]}...",
                        "success": False,
                        "url": url,
                        "is_verified": False,
                        "rejection_reason": rejection_reason,
                        "verdict": verdict.value if isinstance(verdict, PageVerdict) else str(verdict),
                        "product_count": classification.get("product_count", 0),
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
                    "is_verified": True,
                    "verdict": verdict.value if isinstance(verdict, PageVerdict) else str(verdict),
                    "product_count": classification.get("product_count", 0),
                    "verification_reason": f"Rendered after 403: {classification.get('reason', 'Page verified successfully')}",
                }
            
            # Normal flow for non-403 status codes
            classification = fetch_result.get("classification", {})
            verdict_str = classification.get("verdict", "error")
            # Convert string to PageVerdict enum
            try:
                verdict = PageVerdict(verdict_str) if isinstance(verdict_str, str) else verdict_str
            except (ValueError, TypeError):
                verdict = PageVerdict.ERROR
            
            # Check if page is good (verified)
            is_verified = self._is_good_url(status, final_url, verdict, classification)
            
            if not fetch_result.get("html") and not fetch_result.get("text"):
                error_msg = fetch_result.get("error", "Unknown error")
                rejection_reason = f"Failed to fetch (status: {status}, error: {error_msg})"
                return {
                    "content": f"Failed to fetch {url} (status: {status}, error: {error_msg}). Try render_url if this is a JavaScript-heavy page.",
                    "success": False,
                    "url": final_url,
                    "status": status,
                    "error": error_msg,
                    "is_verified": False,
                    "rejection_reason": rejection_reason,
                }
            
            if not is_verified:
                rejection_reason = classification.get("reason", "Page did not pass verification")
                if verdict == PageVerdict.LISTING_EMPTY:
                    rejection_reason = "Empty listing page (no products found)"
                elif verdict == PageVerdict.BLOCKED:
                    rejection_reason = "Page is blocked or requires verification"
                elif verdict == PageVerdict.GENERIC:
                    rejection_reason = "Generic redirect page"
                elif verdict == PageVerdict.ERROR:
                    rejection_reason = "Page classification error"
                
                text = fetch_result.get("text", extract_text(fetch_result.get("html", "")))
                title = fetch_result.get("title", "")
                
                return {
                    "content": f"Fetched {url} but page is not valid: {rejection_reason}.\n\nPage content:\n{text[:500]}...",
                    "success": False,
                    "url": final_url,
                    "title": title,
                    "is_verified": False,
                    "rejection_reason": rejection_reason,
                    "verdict": verdict.value if isinstance(verdict, PageVerdict) else str(verdict),
                    "product_count": classification.get("product_count", 0),
                }

            fetched_urls.add(final_url)
            text = fetch_result.get("text", extract_text(fetch_result.get("html", "")))
            title = fetch_result.get("title", "")
            extracted_links = fetch_result.get("extracted_links", [])
            product_candidate_links = classification.get("product_candidate_links", [])
            canonical_url = fetch_result.get("canonical_url")
            
            # Extract SKU for product pages
            sku = None
            if verdict == PageVerdict.PRODUCT:
                from ..extract.sku_extract import extract_sku
                sku = extract_sku(fetch_result.get("html", ""), text)
            
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
                "is_verified": True,
                "verdict": verdict.value if isinstance(verdict, PageVerdict) else str(verdict),
                "product_count": classification.get("product_count", 0),
                "verification_reason": classification.get("reason", "Page verified successfully"),
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

            # Verify the page
            classification = render_result.get("classification", {})
            verdict_str = classification.get("verdict", "error")
            # Convert string to PageVerdict enum
            try:
                verdict = PageVerdict(verdict_str) if isinstance(verdict_str, str) else verdict_str
            except (ValueError, TypeError):
                verdict = PageVerdict.ERROR
            
            if not render_result.get("text") and not render_result.get("html"):
                rejection_reason = "Failed to render (timeout or error)"
                return {
                    "content": f"Failed to render {url}. Page may be blocked or timeout occurred.",
                    "success": False,
                    "url": url,
                    "is_verified": False,
                    "rejection_reason": rejection_reason,
                }
            
            # Check if page is good (verified)
            is_verified = self._is_good_url(200, url, verdict, classification)
            
            if not is_verified:
                rejection_reason = classification.get("reason", "Page did not pass verification")
                if verdict == PageVerdict.LISTING_EMPTY:
                    rejection_reason = "Empty listing page (no products found)"
                elif verdict == PageVerdict.BLOCKED:
                    rejection_reason = "Page is blocked or requires verification"
                elif verdict == PageVerdict.GENERIC:
                    rejection_reason = "Generic redirect page"
                elif verdict == PageVerdict.ERROR:
                    rejection_reason = "Page classification error"
                
                text = render_result.get("text", "")
                
                return {
                    "content": f"Rendered {url} but page is not valid: {rejection_reason}.\n\nPage content:\n{text[:500]}...",
                    "success": False,
                    "url": url,
                    "is_verified": False,
                    "rejection_reason": rejection_reason,
                    "verdict": verdict.value if isinstance(verdict, PageVerdict) else str(verdict),
                    "product_count": classification.get("product_count", 0),
                }

            fetched_urls.add(url)
            text = render_result.get("text", "")
            extracted_links = render_result.get("extracted_links", [])
            product_candidate_links = classification.get("product_candidate_links", [])
            final_url = render_result.get("final_url", url)
            canonical_url = render_result.get("canonical_url")
            
            # Extract SKU for product pages
            sku = None
            if verdict == PageVerdict.PRODUCT:
                from ..extract.sku_extract import extract_sku
                sku = extract_sku(render_result.get("html", ""), text)
            
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
                "url": final_url,  # Use final_url as the canonical URL
                "final_url": final_url,
                "canonical_url": canonical_url,
                "sku": sku,
                "extracted_links": extracted_links,
                "product_candidate_links": product_candidate_links,
                "is_verified": True,
                "verdict": verdict.value if isinstance(verdict, PageVerdict) else str(verdict),
                "product_count": classification.get("product_count", 0),
                "verification_reason": classification.get("reason", "Page verified successfully"),
            }

        else:
            return {
                "content": f"Unknown tool: {function_name}",
                "success": False,
            }
    
    def _is_good_url(self, status: int, final_url: str, verdict: PageVerdict, classification: Dict[str, Any]) -> bool:  # noqa: ARG002
        """
        Determine if a URL is "good" (verified) based on status, redirect, and classification.
        
        Args:
            status: HTTP status code
            final_url: Final URL after redirects
            verdict: Page classification verdict
            classification: Full classification dict
            
        Returns:
            True if URL is verified/good, False otherwise
        """
        # Must have 2xx status
        if not (200 <= status < 300):
            return False
        
        # Check for generic redirects
        from urllib.parse import urlparse
        parsed = urlparse(final_url)
        path_lower = parsed.path.lower()
        generic_paths = ["/", "/home", "/index", "/search", "/category"]
        if any(path_lower == gp or path_lower.startswith(gp + "/") for gp in generic_paths):
            # Only allow if it has products
            if verdict not in (PageVerdict.PRODUCT, PageVerdict.LISTING_WITH_PRODUCTS):
                return False
        
        # Reject blocked, empty listings, generic, and error pages
        if verdict in (PageVerdict.BLOCKED, PageVerdict.LISTING_EMPTY, PageVerdict.GENERIC, PageVerdict.ERROR):
            return False
        
        # Accept product pages and listings with products
        if verdict in (PageVerdict.PRODUCT, PageVerdict.LISTING_WITH_PRODUCTS):
            return True
        
        # Default: reject if we can't classify it properly
        return False
    
    def _sanitize_output(self, answer: str, verified_urls: Dict[str, Dict[str, Any]]) -> str:
        """
        Remove unverified URLs from the final answer.
        
        Args:
            answer: LLM's final answer text
            verified_urls: Dict of verified URLs
            
        Returns:
            Sanitized answer with only verified URLs
        """
        import re
        from urllib.parse import urlparse
        
        verified_url_set = set(verified_urls.keys())
        
        # Find all URLs in the answer
        url_pattern = r'https?://[^\s<>"\'\)]+'
        urls_found = re.findall(url_pattern, answer)
        
        for url in urls_found:
            # Normalize URL (remove fragment, trailing punctuation, clean tracking params)
            from ..extract.url_cleaner import clean_url
            from urllib.parse import urlparse
            normalized = clean_url(url.rstrip('.,;!?)'), remove_tracking=True)
            
            # Check if URL is verified
            if normalized not in verified_url_set:
                # Try to find a verified URL on the same domain
                parsed = urlparse(normalized)
                domain = parsed.netloc
                replacement = None
                for verified_url in verified_url_set:
                    verified_parsed = urlparse(verified_url)
                    if verified_parsed.netloc == domain:
                        replacement = verified_url
                        break
                
                if replacement:
                    # Replace with verified URL
                    answer = answer.replace(url, replacement)
                    logger.info(f"Replaced unverified URL {url} with verified {replacement}")
                else:
                    # Remove the URL
                    answer = answer.replace(url, "[URL removed - not verified]")
                    logger.info(f"Removed unverified URL: {url}")
        
        return answer
    
    def _get_verified_sources_only(self, verified_urls: Dict[str, Dict[str, Any]]) -> List[Dict[str, str]]:
        """
        Get sources list containing only verified URLs.
        
        Args:
            verified_urls: Dict of verified URLs with metadata
            
        Returns:
            List of source dicts: [{url, title}]
        """
        sources = []
        for url, metadata in verified_urls.items():
            # Use canonical_url if available, otherwise final_url, otherwise original url
            display_url = metadata.get("canonical_url") or metadata.get("final_url") or url
            sources.append({
                "url": display_url,
                "title": metadata.get("title", display_url),
            })
        return sources
    
    def _verify_product_urls(
        self,
        verified_urls: Dict[str, Dict[str, Any]],
        fetched_urls: Set[str],
        max_pages_fetched: int,
    ) -> Dict[str, Dict[str, Any]]:
        """
        Final URL correctness gate: verify product URLs match expected content.
        
        For each verified product URL:
        - If it's a PRODUCT page, verify it's actually a product page
        - If SKU/article mismatch detected, replace with canonical/final URL
        
        Args:
            verified_urls: Dict of verified URLs
            fetched_urls: Set of already fetched URLs (to avoid re-fetching)
            max_pages_fetched: Maximum pages to fetch
            
        Returns:
            Updated verified_urls dict with corrected URLs
        """
        from ..extract.sku_extract import extract_sku
        
        corrected_urls = {}
        
        for url, metadata in verified_urls.items():
            verdict = metadata.get("verdict")
            
            # Only verify PRODUCT pages (not listings)
            if verdict != "product":
                corrected_urls[url] = metadata
                continue
            
            # Check if we have canonical or final URL that's different
            canonical_url = metadata.get("canonical_url")
            final_url = metadata.get("final_url", url)
            
            # Prefer canonical URL if it exists and is different
            preferred_url = canonical_url if (canonical_url and canonical_url != url) else final_url
            
            # If we have a different preferred URL, verify it
            if preferred_url != url and preferred_url not in fetched_urls:
                if len(fetched_urls) < max_pages_fetched:
                    logger.info(f"Verifying preferred URL for product: {preferred_url} (original: {url})")
                    try:
                        # Quick fetch to verify
                        fetch_result = self.fetch_client.fetch(preferred_url)
                        if fetch_result.get("status") == 200:
                            classification = fetch_result.get("classification", {})
                            if classification.get("verdict") == "product":
                                # Preferred URL is valid product page
                                fetched_urls.add(preferred_url)
                                
                                # Extract SKU from both URLs to check for mismatch
                                original_sku = metadata.get("sku")
                                preferred_html = fetch_result.get("html", "")
                                preferred_text = fetch_result.get("text", "")
                                preferred_sku = extract_sku(preferred_html, preferred_text)
                                
                                # If SKUs match or both are None, use preferred URL
                                if original_sku == preferred_sku or (original_sku is None and preferred_sku is None):
                                    metadata["url"] = preferred_url
                                    metadata["final_url"] = preferred_url
                                    metadata["canonical_url"] = fetch_result.get("canonical_url", canonical_url)
                                    metadata["sku"] = preferred_sku
                                    logger.info(f"Using preferred URL: {preferred_url} instead of {url}")
                                else:
                                    logger.warning(f"SKU mismatch: original={original_sku}, preferred={preferred_sku}, keeping original URL")
                    except Exception as e:
                        logger.debug(f"Could not verify preferred URL {preferred_url}: {e}")
            
            corrected_urls[url] = metadata
        
        return corrected_urls
