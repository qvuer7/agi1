# Where URLs Are Checked/Verified

## Overview

URL verification happens in **3 main places**:

1. **During page fetch/render** - Page is classified and verified
2. **In agent loop** - Verification result is checked and tracked
3. **In final output** - Unverified URLs are removed from LLM's answer

---

## 1. Page Classification (When Fetching/Rendering)

### Location 1a: `fetch_client.py` - When fetching via HTTP

**File:** `src/agi/clients/fetch_client.py:48-49`

```python
# Classify page
classification = classify_page(html, text, final_url)
```

**What happens:**
- After fetching page via HTTP
- Page is classified: `PRODUCT`, `LISTING_WITH_PRODUCTS`, `LISTING_EMPTY`, `BLOCKED`, `GENERIC`, `ERROR`
- Classification includes: verdict, product_count, reason, signals

### Location 1b: `browser_client.py` - When rendering with Playwright

**File:** `src/agi/clients/browser_client.py:70-71`

```python
# Classify page
classification = classify_page(html, text, url)
```

**What happens:**
- After rendering page with Playwright (for JS-heavy pages)
- Same classification process as HTTP fetch

### Location 1c: `page_classifier.py` - The actual classification logic

**File:** `src/agi/extract/page_classifier.py:26-319`

**What it checks:**
- JSON-LD Product schemas
- Microdata Product detection
- Product-like link patterns
- Empty listing detection (listing URL + no products)
- Blocked page detection (captcha/interstitial)
- Generic redirect detection

---

## 2. URL Verification (In Agent Loop)

### Location 2a: `agent_loop.py` - After fetching page

**File:** `src/agi/agent/agent_loop.py:291-303`

```python
# Verify the page
status = fetch_result.get("status", 0)
final_url = fetch_result.get("final_url", url)
classification = fetch_result.get("classification", {})
verdict_str = classification.get("verdict", "error")
verdict = PageVerdict(verdict_str)

# Check if page is good (verified)
is_verified = self._is_good_url(status, final_url, verdict, classification)
```

**What happens:**
- Gets classification from fetch result
- Calls `_is_good_url()` to verify
- If not verified → marks as rejected with reason
- If verified → marks as verified, adds to `verified_urls`

### Location 2b: `agent_loop.py` - The verification logic

**File:** `src/agi/agent/agent_loop.py:449-485`

```python
def _is_good_url(self, status: int, final_url: str, verdict: PageVerdict, classification: Dict[str, Any]) -> bool:
    # Must have 2xx status
    if not (200 <= status < 300):
        return False
    
    # Check for generic redirects
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
    
    return False
```

**Verification criteria:**
1. ✅ HTTP status 2xx
2. ✅ Not generic redirect (unless has products)
3. ✅ Not blocked/captcha page
4. ✅ Not empty listing
5. ✅ Verdict is `PRODUCT` or `LISTING_WITH_PRODUCTS`

### Location 2c: `agent_loop.py` - Tracking verified/rejected URLs

**File:** `src/agi/agent/agent_loop.py:152-167`

```python
# Track provenance and sources
if "url" in tool_result:
    url = tool_result["url"]
    attempted_urls.add(url)
    
    # Check if URL is verified (good)
    if tool_result.get("is_verified"):
        verified_urls[url] = {
            "url": url,
            "title": tool_result.get("title", url),
            "verdict": tool_result.get("verdict"),
            "product_count": tool_result.get("product_count", 0),
            "reason": tool_result.get("verification_reason", ""),
        }
    elif tool_result.get("rejection_reason"):
        rejected_urls[url] = tool_result.get("rejection_reason")
```

**What happens:**
- Every URL that's fetched is tracked in `attempted_urls`
- If `is_verified=True` → added to `verified_urls` dict
- If `is_verified=False` → added to `rejected_urls` dict with reason

---

## 3. Output Sanitization (Final Answer)

### Location 3a: `agent_loop.py` - Before returning final answer

**File:** `src/agi/agent/agent_loop.py:212-213`

```python
# Sanitize output to remove unverified URLs
final_answer = self._sanitize_output(final_answer, verified_urls)
```

**When it happens:**
- After LLM gives final answer
- Before returning to user

### Location 3b: `agent_loop.py` - The sanitization logic

**File:** `src/agi/agent/agent_loop.py:487-537`

```python
def _sanitize_output(self, answer: str, verified_urls: Dict[str, Dict[str, Any]]) -> str:
    verified_url_set = set(verified_urls.keys())
    
    # Find all URLs in the answer
    url_pattern = r'https?://[^\s<>"\'\)]+'
    urls_found = re.findall(url_pattern, answer)
    
    for url in urls_found:
        # Normalize URL
        parsed = urlparse(url.rstrip('.,;!?)'))
        normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        if parsed.query:
            normalized += f"?{parsed.query}"
        
        # Check if URL is verified
        if normalized not in verified_url_set:
            # Try to find a verified URL on the same domain
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
            else:
                # Remove the URL
                answer = answer.replace(url, "[URL removed - not verified]")
    
    return answer
```

**What it does:**
1. Extracts all URLs from LLM's final answer
2. Checks each URL against `verified_urls` set
3. If not verified:
   - Tries to replace with verified URL on same domain
   - If no replacement found → removes URL
4. Returns sanitized answer

---

## Flow Diagram

```
User Request
    ↓
Agent Loop Starts
    ↓
LLM calls fetch_url("https://example.com/product")
    ↓
┌─────────────────────────────────────────┐
│ 1. fetch_client.fetch()                 │
│    - HTTP GET request                    │
│    - Extract HTML                        │
│    - classify_page() ← CHECK HERE       │
│      • Detects products?                 │
│      • Empty listing?                    │
│      • Blocked page?                     │
└─────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────┐
│ 2. agent_loop._execute_tool()           │
│    - Get classification                  │
│    - _is_good_url() ← VERIFY HERE       │
│      • Status 2xx?                       │
│      • Not generic redirect?             │
│      • Not blocked/empty?                │
│    - Set is_verified=True/False         │
└─────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────┐
│ 3. Track in verified_urls/rejected_urls │
│    - If verified → add to verified_urls│
│    - If rejected → add to rejected_urls│
└─────────────────────────────────────────┘
    ↓
LLM gets page content (or rejection message)
    ↓
LLM gives final answer with URLs
    ↓
┌─────────────────────────────────────────┐
│ 4. _sanitize_output() ← CLEAN HERE      │
│    - Extract URLs from answer           │
│    - Remove unverified URLs              │
│    - Replace with verified if possible  │
└─────────────────────────────────────────┘
    ↓
Return to user (only verified URLs)
```

---

## Summary

**URL checking happens at:**

1. **`fetch_client.py:49`** / **`browser_client.py:71`**
   - Page classification when fetching/rendering

2. **`agent_loop.py:303`** / **`agent_loop.py:403`**
   - Verification check after fetch/render

3. **`agent_loop.py:449`** (`_is_good_url()`)
   - Actual verification logic

4. **`agent_loop.py:487`** (`_sanitize_output()`)
   - Final cleanup of LLM's answer

**Key files:**
- `src/agi/extract/page_classifier.py` - Classification logic
- `src/agi/agent/agent_loop.py` - Verification and sanitization
- `src/agi/clients/fetch_client.py` - Triggers classification on fetch
- `src/agi/clients/browser_client.py` - Triggers classification on render
