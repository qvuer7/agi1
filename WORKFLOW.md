# AGI-1 Agent Workflow Documentation

This document describes the complete workflow of the web browsing agent, with references to the actual code implementation.

## Table of Contents

1. [Overview](#overview)
2. [API Entry Point](#api-entry-point)
3. [Agent Loop Flow](#agent-loop-flow)
4. [Available Tools](#available-tools)
5. [Tool Execution Details](#tool-execution-details)
6. [Response Generation](#response-generation)

---

## Overview

The agent is a web browsing assistant that:
1. Takes a user prompt via REST API
2. Uses an LLM (OpenRouter) to decide which tools to call
3. Executes tools (search, fetch, render) to gather information
4. Returns a synthesized answer with sources

**Main Entry Point:** `src/agi/api/main.py` → `/browse` endpoint

---

## API Entry Point

### POST `/browse`

**Location:** `src/agi/api/main.py:55-85`

**Request Model:** `src/agi/api/models.py:7-20`
```python
{
    "prompt": str,           # User query
    "mode": "auto",          # Optional: "auto"|"search"|"browser"
    "max_steps": 10          # Optional: max agent steps (1-20)
}
```

**Response Model:** `src/agi/api/models.py:30-35`
```python
{
    "answer": str,           # Final answer from agent
    "sources": [             # List of verified URLs
        {"url": str, "title": str}
    ],
    "debug": dict            # Optional debug traces
}
```

**Flow:**
1. Receives request → `src/agi/api/main.py:62`
2. Gets agent instance (lazy init) → `src/agi/api/main.py:66`
3. Calls `agent.run()` → `src/agi/api/main.py:69-72`
4. Formats response → `src/agi/api/main.py:75-80`

---

## Agent Loop Flow

### Main Loop: `AgentLoop.run()`

**Location:** `src/agi/agent/agent_loop.py:37-192`

#### Step 1: Initialize Messages

**Location:** `src/agi/agent/agent_loop.py:54-66`

```python
messages = [
    {
        "role": "system",
        "content": "You are a reliable web browsing and product research assistant..."
    },
    {
        "role": "user", 
        "content": user_prompt
    }
]
```

**System Prompt Rules:** `src/agi/agent/agent_loop.py:57-70`
- Use `search_web` + `fetch_url` (prefer over `render_url`)
- **CRITICAL LINK VALIDATION RULES:** Must verify all links via tools
- Never hallucinate URLs
- Only cite URLs that were successfully fetched/rendered
- If link returns 403, 404, timeout, or empty content → discard it
- If no verified link available → explicitly say "no verified listing found"

#### Step 2: Agent Iteration Loop

**Location:** `src/agi/agent/agent_loop.py:73-154`

For each step (up to `max_steps`):

1. **Call LLM** → `src/agi/agent/agent_loop.py:78`
   - Uses OpenRouter API → `src/agi/clients/openrouter_client.py:35-80`
   - Passes messages + tools → `src/agi/agent/tool_schemas.py:3-62`

2. **Check for Final Answer** → `src/agi/agent/agent_loop.py:88-96`
   - If no `tool_calls` → return answer immediately
   - Otherwise, process tool calls

3. **Process Tool Calls** → `src/agi/agent/agent_loop.py:101-146`
   - Parse tool call arguments → `src/agi/agent/agent_loop.py:106-111`
   - Execute tool → `src/agi/agent/agent_loop.py:116-121`
   - Add tool result to messages → `src/agi/agent/agent_loop.py:124-128`
   - Track sources → `src/agi/agent/agent_loop.py:130-137`

4. **Check Limits** → `src/agi/agent/agent_loop.py:148-154`
   - If `max_pages_fetched` reached → prompt LLM to finish

#### Step 3: Max Steps Reached

**Location:** `src/agi/agent/agent_loop.py:156-183`

If step limit reached:
- Try to extract last assistant message → `src/agi/agent/agent_loop.py:160-166`
- Generate summary from sources if no message → `src/agi/agent/agent_loop.py:168-180`
- Return partial results

---

## Available Tools

The agent has access to 3 tools, defined in: `src/agi/agent/tool_schemas.py:3-62`

### 1. `search_web`

**Tool Definition:** `src/agi/agent/tool_schemas.py:4-26`

**Parameters:**
- `query` (str, required): Search query string
- `count` (int, default=5, max=10): Number of results

**Implementation:** `src/agi/agent/agent_loop.py:202-235`

**Flow:**
1. Check cache → `src/agi/cache/cache.py:30-35`
2. If not cached, call Brave Search API → `src/agi/clients/brave_client.py:30-73`
3. Cache results → `src/agi/cache/cache.py:37-41`
4. Format results for LLM → `src/agi/agent/agent_loop.py:227-234`

**Brave Search Client:** `src/agi/clients/brave_client.py`
- API Endpoint: `https://api.search.brave.com/res/v1/web/search`
- Returns: `[{title, url, snippet}]`

### 2. `fetch_url`

**Tool Definition:** `src/agi/agent/tool_schemas.py:28-43`

**Parameters:**
- `url` (str, required): URL to fetch

**Implementation:** `src/agi/agent/agent_loop.py:237-280`

**Flow:**
1. Check cache → `src/agi/cache/cache.py:44-49`
2. If not cached, fetch via HTTP → `src/agi/clients/fetch_client.py:24-108`
3. Extract text from HTML → `src/agi/extract/html_extract.py:13-54`
4. Cache result → `src/agi/cache/cache.py:51-55`
5. Return content to LLM → `src/agi/agent/agent_loop.py:275-280`

**Fetch Client:** `src/agi/clients/fetch_client.py`
- Uses `httpx` for HTTP requests
- Extracts title from HTML → `src/agi/clients/fetch_client.py:95-108`
- Returns: `{status, final_url, html, title, text, error}`

**HTML Extraction:** `src/agi/extract/html_extract.py`
- Primary: `trafilatura` library
- Fallback: `BeautifulSoup` + `lxml`
- Truncates to `MAX_PAGE_TEXT_LENGTH` (20,000 chars)

### 3. `render_url`

**Tool Definition:** `src/agi/agent/tool_schemas.py:45-61`

**Parameters:**
- `url` (str, required): URL to render

**Implementation:** `src/agi/agent/agent_loop.py:282-319`

**Flow:**
1. Check cache → `src/agi/cache/cache.py:58-63`
2. If not cached, render with Playwright → `src/agi/clients/browser_client.py:31-116`
3. Extract text from HTML → `src/agi/extract/html_extract.py:13-54`
4. Cache result → `src/agi/cache/cache.py:65-69`
5. Return content to LLM → `src/agi/agent/agent_loop.py:315-319`

**Browser Client:** `src/agi/clients/browser_client.py`
- Uses Playwright (headless Chromium)
- Async-safe wrapper → `src/agi/clients/browser_client.py:88-116`
- Runs in thread pool to avoid asyncio conflicts
- Waits for `networkidle` state
- Returns: `{html, text}`

---

## Tool Execution Details

### Tool Execution Function

**Location:** `src/agi/agent/agent_loop.py:194-325`

**Function:** `AgentLoop._execute_tool()`

**Parameters:**
- `function_name`: Tool name (`search_web`, `fetch_url`, `render_url`)
- `args`: Tool arguments (dict)
- `fetched_urls`: Set of already fetched URLs (for limit tracking)
- `max_pages_fetched`: Maximum pages to fetch

**Returns:**
```python
{
    "content": str,      # Formatted result for LLM
    "success": bool,     # Whether tool succeeded
    "url": str,          # URL (if applicable)
    "title": str         # Page title (if applicable)
}
```

### Caching Strategy

**Cache Implementation:** `src/agi/cache/cache.py:14-75`

**Cache Types:**
1. **Search Cache** → `src/agi/cache/cache.py:30-41`
   - Key: `search:{query}`
   - TTL: 1 day (`SEARCH_CACHE_TTL = 86400`)
   
2. **Fetch Cache** → `src/agi/cache/cache.py:44-55`
   - Key: `fetch:{url}`
   - TTL: 7 days (`FETCH_CACHE_TTL = 604800`)
   
3. **Render Cache** → `src/agi/cache/cache.py:58-69`
   - Key: `render:{url}`
   - TTL: 7 days (`FETCH_CACHE_TTL = 604800`)

**Storage:** Disk-based using `diskcache` library

---

## Response Generation

### Final Answer Processing

**Location:** `src/agi/agent/agent_loop.py:88-96` (normal completion)
**Location:** `src/agi/agent/agent_loop.py:156-183` (max steps reached)

**Sources Tracking:**
- URLs are tracked when fetched/rendered → `src/agi/agent/agent_loop.py:130-137`
- Sources list built incrementally during tool execution
- Final response includes all sources → `src/agi/api/main.py:75`

### Response Format

**Final Response Structure:**
```python
{
    "answer": str,              # LLM-generated answer
    "sources": [                # All URLs that were fetched/rendered
        {
            "url": str,
            "title": str
        }
    ],
    "debug": [                  # Optional: tool execution traces
        {
            "step": int,
            "tool": str,
            "args": dict,
            "result_length": int,
            "success": bool
        }
    ]
}
```

---

## Configuration

**Location:** `src/agi/config.py`

**Key Settings:**
- `DEFAULT_MAX_STEPS = 10` → Max agent iterations
- `DEFAULT_MAX_PAGES_FETCHED = 8` → Max pages to fetch
- `MAX_PAGE_TEXT_LENGTH = 20000` → Text truncation limit
- `SEARCH_CACHE_TTL = 86400` → 1 day
- `FETCH_CACHE_TTL = 604800` → 7 days

**Environment Variables:**
- `OPENROUTER_API_KEY` → LLM API key
- `BRAVE_API_KEY` → Search API key
- `OR_MODEL` → Model name (default: `google/gemini-2.0-flash`)
- `CACHE_DIR` → Cache directory (default: `.cache`)

---

## Error Handling

### LLM Errors
- **Location:** `src/agi/agent/agent_loop.py:77-85`
- Returns error message in answer field

### Tool Errors
- **Search failures:** `src/agi/agent/agent_loop.py:214-219`
- **Fetch failures:** `src/agi/agent/agent_loop.py:260-268`
- **Render failures:** `src/agi/agent/agent_loop.py:305-310`
- All errors are returned in tool result `content` field for LLM to see

### API Errors
- **Location:** `src/agi/api/main.py:83-85`
- Returns HTTP 500 with error details

---

## Example Workflow

1. **User Request:**
   ```
   POST /browse
   {"prompt": "find 55-inch OLED TV in Sofia"}
   ```

2. **Agent Step 1:**
   - LLM calls `search_web("55-inch OLED TV Sofia")`
   - Gets 5 results from Brave Search
   - Results added to messages

3. **Agent Step 2:**
   - LLM calls `fetch_url("https://technopolis.bg/...")`
   - Page fetched, text extracted
   - Content added to messages

4. **Agent Step 3-5:**
   - LLM fetches more URLs
   - Synthesizes information

5. **Agent Step 6:**
   - LLM returns final answer (no more tool calls)
   - Response sent to user with all sources

---

## Key Files Reference

| Component | File | Key Functions |
|-----------|------|---------------|
| API Entry | `src/agi/api/main.py` | `browse()`, `get_agent()` |
| Agent Loop | `src/agi/agent/agent_loop.py` | `run()`, `_execute_tool()` |
| Tool Schemas | `src/agi/agent/tool_schemas.py` | `TOOLS` constant |
| OpenRouter Client | `src/agi/clients/openrouter_client.py` | `chat()` |
| Brave Client | `src/agi/clients/brave_client.py` | `search()` |
| Fetch Client | `src/agi/clients/fetch_client.py` | `fetch()` |
| Browser Client | `src/agi/clients/browser_client.py` | `render()` |
| HTML Extract | `src/agi/extract/html_extract.py` | `extract_text()` |
| Cache | `src/agi/cache/cache.py` | `get_*()`, `set_*()` |
| Config | `src/agi/config.py` | All configuration constants |

---

## Notes

- **No Link Verification:** Link validation was removed per user request. All fetched URLs are included in sources.
- **Async Safety:** Browser client uses thread pool to avoid asyncio conflicts with FastAPI.
- **Caching:** All tool results are cached to reduce API calls and improve performance.
- **Text Truncation:** Page text is truncated to 20k chars before sending to LLM to stay within context limits.
