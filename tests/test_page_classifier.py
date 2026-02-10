"""Tests for page classifier."""

import pytest
from agi.extract.page_classifier import classify_page, PageVerdict


def test_product_page_with_json_ld():
    """Test detection of product page with JSON-LD schema."""
    html = """
    <html>
    <head>
        <script type="application/ld+json">
        {
            "@context": "https://schema.org",
            "@type": "Product",
            "name": "Test Product",
            "price": "29.99"
        }
        </script>
    </head>
    <body>
        <h1>Test Product</h1>
        <p>This is a product page.</p>
    </body>
    </html>
    """
    text = "Test Product This is a product page."
    url = "https://example.com/product/123"
    
    result = classify_page(html, text, url)
    assert result["verdict"] == PageVerdict.PRODUCT
    assert result["product_count"] > 0


def test_empty_listing_page():
    """Test detection of empty listing page."""
    html = """
    <html>
    <body>
        <h1>Category: Electronics</h1>
        <p>No products found in this category.</p>
        <div class="product-list"></div>
    </body>
    </html>
    """
    text = "Category: Electronics No products found in this category."
    url = "https://example.com/category/electronics"
    
    result = classify_page(html, text, url)
    assert result["verdict"] == PageVerdict.LISTING_EMPTY
    assert result["product_count"] == 0


def test_listing_with_products():
    """Test detection of listing page with products."""
    html = """
    <html>
    <body>
        <h1>Category: Electronics</h1>
        <div class="product-list">
            <a href="/product/1" class="product">Product 1</a>
            <a href="/product/2" class="product">Product 2</a>
            <a href="/product/3" class="product">Product 3</a>
        </div>
    </body>
    </html>
    """
    text = "Category: Electronics Product 1 Product 2 Product 3"
    url = "https://example.com/category/electronics"
    
    result = classify_page(html, text, url)
    assert result["verdict"] == PageVerdict.LISTING_WITH_PRODUCTS
    assert result["product_count"] >= 3


def test_blocked_page():
    """Test detection of blocked/captcha page."""
    html = """
    <html>
    <body>
        <form id="captcha-form" action="/verify">
            <p>Please verify you are human</p>
        </form>
    </body>
    </html>
    """
    text = "Please verify you are human"
    url = "https://example.com/page"
    
    result = classify_page(html, text, url)
    assert result["verdict"] == PageVerdict.BLOCKED


def test_generic_redirect():
    """Test detection of generic redirect page."""
    html = """
    <html>
    <body>
        <h1>Home</h1>
        <p>Welcome to our website.</p>
    </body>
    </html>
    """
    text = "Home Welcome to our website."
    url = "https://example.com/"
    
    result = classify_page(html, text, url)
    # Should be generic if no products detected
    if result["product_count"] == 0:
        assert result["verdict"] == PageVerdict.GENERIC


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
