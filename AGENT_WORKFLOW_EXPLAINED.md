# How the Agent Really Works - Step by Step

## Example: User asks "Find iPhone 15 on rozetka.ua"

### Step 1: User sends request
```
POST /browse
{
  "prompt": "Find iPhone 15 on rozetka.ua"
}
```

### Step 2: Agent initializes conversation

**Location:** `src/agi/agent/agent_loop.py:53-81`

The agent creates a message history:
```python
messages = [
    {
        "role": "system",
        "content": "You are a reliable web browsing assistant. Use search_web + fetch_url..."
    },
    {
        "role": "user", 
        "content": "Find iPhone 15 on rozetka.ua"  # ← Your prompt
    }
]
```

**Key point:** The LLM sees:
- System instructions (how to use tools)
- Your user prompt (what to find)
- **NO direct API calls yet** - LLM decides what to do

---

### Step 3: First LLM call (OpenRouter)

**Location:** `src/agi/agent/agent_loop.py:96-97`

```python
assistant_message = self.or_client.chat(messages, tools=TOOLS)
```

**What happens:**
1. Agent sends messages + tool definitions to OpenRouter API
2. LLM analyzes: "User wants iPhone 15 on rozetka.ua"
3. LLM decides: "I should search for this"
4. LLM returns: **Tool call** (not final answer)

**LLM response looks like:**
```json
{
  "content": null,
  "tool_calls": [
    {
      "id": "call_123",
      "function": {
        "name": "search_web",
        "arguments": "{\"query\": \"iPhone 15 rozetka.ua\", \"count\": 5}"
      }
    }
  ]
}
```

**Key point:** The LLM **decides** to call `search_web` - the agent doesn't create the search query, the LLM does!

---

### Step 4: Agent executes tool (Brave Search)

**Location:** `src/agi/agent/agent_loop.py:132-143`

```python
tool_result = self._execute_tool(
    "search_web",
    {"query": "iPhone 15 rozetka.ua", "count": 5},
    ...
)
```

**What happens:**
1. Agent calls `brave_client.search("iPhone 15 rozetka.ua", count=5)`
2. Brave Search API returns results:
   ```json
   [
     {"title": "iPhone 15 - Rozetka", "url": "https://rozetka.ua/iphone15/p12345/", "snippet": "..."},
     {"title": "iPhone 15 Pro - Rozetka", "url": "https://rozetka.ua/iphone15pro/p67890/", "snippet": "..."},
     ...
   ]
   ```
3. Agent formats results for LLM:
   ```
   Found 5 search results:
   
   - iPhone 15 - Rozetka
     URL: https://rozetka.ua/iphone15/p12345/
     ...
   ```

**Key point:** Agent does NOT call browser yet - just gets search results!

---

### Step 5: Feed results back to LLM

**Location:** `src/agi/agent/agent_loop.py:146-150`

```python
messages.append({
    "role": "tool",
    "tool_call_id": "call_123",
    "content": "Found 5 search results:\n\n- iPhone 15 - Rozetka\n  URL: https://rozetka.ua/iphone15/p12345/\n  ..."
})
```

**Message history now:**
```python
[
    {"role": "system", "content": "..."},
    {"role": "user", "content": "Find iPhone 15 on rozetka.ua"},
    {"role": "assistant", "tool_calls": [...]},  # ← LLM said "search"
    {"role": "tool", "content": "Found 5 search results..."}  # ← Agent executed search
]
```

---

### Step 6: Second LLM call

**Location:** `src/agi/agent/agent_loop.py:96-97` (loop continues)

```python
assistant_message = self.or_client.chat(messages, tools=TOOLS)
```

**What LLM sees:**
- Original user request
- Search results with URLs
- LLM thinks: "I found URLs, but I need to verify them by fetching the pages"

**LLM response:**
```json
{
  "tool_calls": [
    {
      "function": {
        "name": "fetch_url",
        "arguments": "{\"url\": \"https://rozetka.ua/iphone15/p12345/\"}"
      }
    }
  ]
}
```

**Key point:** LLM decides to fetch the URL - agent doesn't decide!

---

### Step 7: Agent fetches the page

**Location:** `src/agi/agent/agent_loop.py:267-351`

```python
tool_result = self._execute_tool(
    "fetch_url",
    {"url": "https://rozetka.ua/iphone15/p12345/"},
    ...
)
```

**What happens:**
1. Agent calls `fetch_client.fetch(url)`
2. HTTP GET request to the URL
3. Extracts HTML → converts to text
4. **NEW:** Classifies page (product? empty listing? blocked?)
5. **NEW:** Verifies if page is "good" (not empty, not blocked)
6. Returns formatted content to LLM:
   ```
   Fetched https://rozetka.ua/iphone15/p12345/:
   
   [Extracted page text - up to 20,000 chars]
   ```

**Key point:** If page is empty/blocked, agent marks it as rejected and tells LLM!

---

### Step 8: Feed page content back to LLM

**Location:** `src/agi/agent/agent_loop.py:146-150`

```python
messages.append({
    "role": "tool",
    "content": "Fetched https://rozetka.ua/iphone15/p12345/:\n\n[page text]"
})
```

---

### Step 9: Third LLM call (final answer or more tools)

**Location:** `src/agi/agent/agent_loop.py:96-107`

```python
assistant_message = self.or_client.chat(messages, tools=TOOLS)

# Check if LLM wants to call more tools or give final answer
if "tool_calls" not in assistant_message or not assistant_message["tool_calls"]:
    # Final answer!
    answer = assistant_message.get("content", "")
    return {"answer": answer, "sources": sources, ...}
```

**LLM response (final answer):**
```json
{
  "content": "I found iPhone 15 on Rozetka.ua:\n\n**iPhone 15 128GB**\nPrice: 35,999 UAH\nURL: https://rozetka.ua/iphone15/p12345/\n\n[more details from page]",
  "tool_calls": null  # ← No more tools needed
}
```

**OR** LLM might call more tools:
- Fetch another URL
- Try `render_url` if fetch failed
- Search again with different query

---

### Step 10: Output sanitization

**Location:** `src/agi/agent/agent_loop.py:212-213`

```python
final_answer = self._sanitize_output(final_answer, verified_urls)
```

**What happens:**
1. Extract all URLs from LLM's answer
2. Check if each URL is in `verified_urls` (was successfully fetched)
3. Remove unverified URLs
4. Optionally replace with verified URL on same domain

**Example:**
- LLM says: "Check https://rozetka.ua/iphone15/p12345/ and https://rozetka.ua/iphone15pro/p67890/"
- Agent verified: Only `p12345/` was successfully fetched
- Final answer: "Check https://rozetka.ua/iphone15/p12345/ and [URL removed - not verified]"

---

## Summary: The Loop

```
1. User prompt → Agent
2. Agent → LLM (with tools available)
3. LLM → Agent (tool call decision)
4. Agent → External API (Brave/Browser/HTTP)
5. Agent → LLM (tool results)
6. Repeat steps 3-5 until LLM gives final answer
7. Agent sanitizes output (removes unverified URLs)
8. Agent → User (final answer + verified sources)
```

## Key Points

1. **LLM decides everything:**
   - What search query to use
   - Which URLs to fetch
   - When to stop and give final answer

2. **Agent executes:**
   - Calls Brave Search API
   - Fetches/renders pages
   - Verifies pages (new feature)
   - Sanitizes output (new feature)

3. **No direct browser calls for search:**
   - Search = Brave Search API (fast, no browser)
   - Browser (Playwright) = Only for JavaScript-heavy pages that fetch_url fails on

4. **Verification happens automatically:**
   - Every fetched/rendered page is classified
   - Empty listings are rejected
   - Blocked pages are rejected
   - Only verified URLs appear in final answer

## Code References

- **Main loop:** `src/agi/agent/agent_loop.py:92-185`
- **LLM call:** `src/agi/agent/agent_loop.py:96-97`
- **Tool execution:** `src/agi/agent/agent_loop.py:135-143`
- **Search tool:** `src/agi/agent/agent_loop.py:234-265`
- **Fetch tool:** `src/agi/agent/agent_loop.py:267-351`
- **Output sanitization:** `src/agi/agent/agent_loop.py:478-523`
