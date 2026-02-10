"""Agent loop for orchestrating tool calls and LLM interactions."""

from typing import Any, Dict, List, Optional, Set
from ..clients.openrouter_client import OpenRouterClient
from ..clients.brave_client import BraveClient
from ..clients.fetch_client import FetchClient
from ..clients.browser_client import BrowserClient
from ..extract.html_extract import extract_text
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
                    "You are a helpful web browsing assistant. "
                    "Use the available tools to search the web and fetch pages. "
                    "Prefer search_web + fetch_url. Use render_url only if fetch_url fails or returns empty content. "
                    "Always cite URLs in your responses. "
                    "Be concise but thorough. If a search doesn't yield good results, try refining the query."
                ),
            },
            {"role": "user", "content": user_prompt},
        ]

        sources: List[Dict[str, str]] = []
        fetched_urls: Set[str] = set()
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
                return {
                    "answer": answer,
                    "sources": sources,
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
                tool_result = self._execute_tool(function_name, function_args, fetched_urls, max_pages_fetched)

                # Add tool result to messages
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.get("id"),
                    "content": tool_result["content"],
                })

                # Track sources
                if "url" in tool_result:
                    url = tool_result["url"]
                    if url not in [s["url"] for s in sources]:
                        sources.append({
                            "url": url,
                            "title": tool_result.get("title", url),
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

        # Max steps reached
        logger.warning(f"Reached max_steps limit ({max_steps})")
        # Get final answer from last assistant message
        final_answer = "I've gathered information from multiple sources, but reached the step limit. "
        if messages and messages[-1].get("role") == "assistant":
            final_answer += messages[-1].get("content", "")
        else:
            final_answer += "Please review the sources provided."

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

            if not fetch_result.get("html") and not fetch_result.get("text"):
                return {
                    "content": f"Failed to fetch {url} (status: {fetch_result.get('status', 0)}). Try render_url if this is a JavaScript-heavy page.",
                    "success": False,
                    "url": url,
                }

            fetched_urls.add(url)
            text = fetch_result.get("text", extract_text(fetch_result.get("html", "")))
            return {
                "content": f"Fetched {url}:\n\n{text}",
                "success": True,
                "url": url,
                "title": fetch_result.get("title", url),
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
                render_result = cached
            else:
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
            text = render_result.get("text", "")
            return {
                "content": f"Rendered {url}:\n\n{text}",
                "success": True,
                "url": url,
            }

        else:
            return {
                "content": f"Unknown tool: {function_name}",
                "success": False,
            }
