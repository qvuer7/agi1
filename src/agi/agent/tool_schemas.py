"""Tool schemas for LLM function calling."""

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
            "description": "Fetch and extract text content from a URL. Use this after search_web to get page content. Use this to verify product pages (fast). For JavaScript-heavy listing pages, use render_url instead.",
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
    {
        "type": "function",
        "function": {
            "name": "render_url",
            "description": "Render a URL using a browser (for JavaScript-heavy pages). Use this for listing/category pages that require JavaScript. Returns product_candidate_links extracted from DOM. Only use if fetch_url fails or returns empty content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL to render",
                    },
                },
                "required": ["url"],
            },
        },
    },
]
