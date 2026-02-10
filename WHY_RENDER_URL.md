# Why Do We Need `render_url`?

## Quick Answer

**`render_url` is needed for JavaScript-heavy listing pages** where product links are loaded dynamically via JS. `fetch_url` only gets the initial HTML, which doesn't have the product links yet.

## The Problem

Many modern e-commerce sites use JavaScript to:
1. Load product listings dynamically (AJAX/fetch)
2. Render product cards after page load
3. Lazy-load images and content

If you use `fetch_url` (simple HTTP GET), you get:
```html
<html>
  <body>
    <div id="products"></div>  <!-- Empty! Products loaded via JS -->
    <script>
      // JavaScript that loads products after page loads
      fetch('/api/products').then(...)
    </script>
  </body>
</html>
```

**Result:** No product links in the HTML! `product_candidate_links` will be empty.

## The Solution: `render_url`

`render_url` uses Playwright (headless browser) to:
1. Execute JavaScript
2. Wait for content to load
3. Extract the **actual rendered DOM** with product links

**Result:** Product links are now in the DOM, can be extracted!

## When to Use Each

### Use `fetch_url` (fast, ~1-2 seconds):
- ✅ Simple product pages (static HTML)
- ✅ Pages that work without JS
- ✅ Quick verification of product URLs
- ✅ When you just need to check if a URL is valid

### Use `render_url` (slower, ~5-20 seconds):
- ✅ **Listing/category pages** (products loaded via JS)
- ✅ Search results pages (dynamic content)
- ✅ Pages that require JS to show products
- ✅ When you need `product_candidate_links` from listing pages

## Example: Why Render is Needed

**Scenario:** Finding products on `sovajewels.com`

1. **Search finds listing page:** `https://sovajewels.com/ua/category/rings/`
2. **If you use `fetch_url`:**
   - Gets initial HTML
   - No product links (they're loaded via JS)
   - `product_candidate_links = []`
   - Page marked as `LISTING_EMPTY` ❌

3. **If you use `render_url`:**
   - Executes JavaScript
   - Products load dynamically
   - Extracts product links from rendered DOM
   - `product_candidate_links = ["/product/1", "/product/2", ...]` ✅
   - Page marked as `LISTING_WITH_PRODUCTS` ✅

## Code Flow

```
Phase 3: Extract product candidates
├─ render_url(listing_page_url)
│  ├─ Playwright loads page
│  ├─ JavaScript executes
│  ├─ Products appear in DOM
│  └─ Extract product_candidate_links[] ← KEY!
│
└─ If product_candidate_links is empty → discard page
```

## Performance Trade-off

- **`fetch_url`:** Fast (~1-2s) but misses JS-loaded content
- **`render_url`:** Slower (~5-20s) but gets full content

**Strategy:** Use `fetch_url` first, fallback to `render_url` if:
- Content is empty
- No product links found
- Page appears to be JS-heavy

## Summary

**You DO need `render_url`** because:
1. Listing pages often require JS to show products
2. `product_candidate_links` extraction needs the rendered DOM
3. Without it, you'll get empty listings even when products exist

**But you DON'T need it for:**
- Simple product pages (static HTML)
- Quick URL verification
- Pages that work without JS

The agent decides automatically based on the page type and content.
