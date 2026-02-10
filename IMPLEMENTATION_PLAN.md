# Implementation Plan: Link Verification and Empty Page Detection

## Files to Modify

1. **src/agi/extract/page_classifier.py** (NEW)
   - `classify_page(html, text, url) -> PageVerdict`
   - Language-agnostic empty listing detection
   - Blocked/captcha detection
   - Generic redirect detection

2. **src/agi/extract/html_extract.py**
   - Add `extract_links(html, base_url) -> List[str]`
   - Extract absolute URLs from page

3. **src/agi/clients/fetch_client.py**
   - Add link extraction to `fetch()` return value
   - Return `extracted_links` field

4. **src/agi/clients/browser_client.py**
   - Add link extraction to `render()` return value
   - Return `extracted_links` field

5. **src/agi/clients/brave_client.py**
   - Add 429 rate limit handling with exponential backoff

6. **src/agi/agent/agent_loop.py**
   - Add provenance tracking: `attempted_urls`, `verified_urls`, `rejected_urls`
   - Integrate page classification
   - Add output sanitizer
   - Add convergence logic

7. **src/agi/config.py**
   - Add convergence settings

## Implementation Steps

### Step 1: Create Page Classifier
- Detect JSON-LD Product schemas
- Count product-like links/anchors
- Detect empty listings (listing URL + no products)
- Detect blocked pages (structural signals)
- Detect generic redirects

### Step 2: Add Link Extraction
- Extract all absolute URLs from HTML
- Deduplicate and limit count
- Return in fetch/render results

### Step 3: Add Provenance Tracking
- Track all attempted URLs
- Classify each fetched page
- Mark as verified/rejected with reason
- Store metadata (verdict, product_count, etc.)

### Step 4: Add Output Sanitization
- Extract URLs from LLM final answer
- Remove unverified URLs
- Optionally replace with verified category URLs

### Step 5: Add Convergence Logic
- Parse user request for expected product count
- Don't return final answer until verified product links >= N
- Label partial results

### Step 6: Add Brave 429 Backoff
- Detect 429 responses
- Exponential backoff retry




https://auto.ria.com/auto_bmw_5_series_39462080.html


curl -X POST "http://localhost:8000/browse"   -H "Content-Type: application/json"   -d '{
    "prompt": "найди 5 максимально похожих товаров на этот: https://auto.ria.com/auto_bmw_5_series_39462080.html только на сайте этой компании: https://auto.ria.com/", "max_steps" : 20
  }'