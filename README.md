# AGI-1: Web Browsing Agent Service

A web browsing agent service that uses Brave Search, HTTP fetch, and Playwright to answer user queries with cited sources.

## Features

- **Web Search**: Uses Brave Search API to find relevant URLs
- **HTTP Fetching**: Fetches and extracts text from web pages
- **Browser Rendering**: Playwright fallback for JavaScript-heavy pages
- **LLM Integration**: OpenRouter for chat completions with tool calling
- **Caching**: Disk-based cache for search results and fetched pages
- **FastAPI API**: RESTful API for querying the agent

## Setup

### 1. Install Dependencies

```bash
uv sync
```

### 2. Install Playwright Browsers

```bash
python -m playwright install chromium
```

### 3. Configure Environment Variables

Copy `.env.example` to `.env` and fill in your API keys:

```bash
cp .env.example .env
```

Edit `.env`:

```
OPENROUTER_API_KEY=your_openrouter_api_key_here
BRAVE_API_KEY=your_brave_api_key_here
OR_MODEL=google/gemini-2.0-flash
USER_AGENT=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36
CACHE_DIR=.cache
```

### 4. Run the Service

```bash
uv run uvicorn agi.api.main:app --reload --port 8000
```

The API will be available at `http://localhost:8000`

## API Endpoints

### POST /browse

Main endpoint for querying the agent.

**Request:**
```json
{
  "prompt": "give me info of a 55-inch mid-range OLED TV, list 5 options with pros/cons and sources",
  "mode": "auto",
  "max_steps": 10
}
```

**Response:**
```json
{
  "answer": "...",
  "sources": [
    {"url": "https://...", "title": "..."}
  ],
  "debug": null
}
```

### GET /health

Health check endpoint.

## Usage Example

```bash
curl -X POST "http://localhost:8000/browse" \
  -H "Content-Type: application/json" \
  -d "{\"prompt\":\"give me info of a 55-inch mid-range OLED TV, list 5 options with pros/cons and sources\"}"
```

## Architecture

```
src/agi/
  ├── config.py          # Configuration from environment variables
  ├── logging.py         # Logging setup
  ├── clients/           # External service clients
  │   ├── openrouter_client.py
  │   ├── brave_client.py
  │   ├── fetch_client.py
  │   └── browser_client.py
  ├── extract/           # Content extraction
  │   ├── html_extract.py
  │   └── product_extract.py
  ├── cache/             # Caching layer
  │   └── cache.py
  ├── agent/             # Agent logic
  │   ├── tool_schemas.py
  │   └── agent_loop.py
  └── api/               # FastAPI application
      ├── models.py
      └── main.py
```

## Configuration

- **Search Cache TTL**: 1 day
- **Fetch/Render Cache TTL**: 7 days
- **Default Max Steps**: 10
- **Default Max Pages Fetched**: 8
- **Max Page Text Length**: 20,000 characters

## Development

The service uses:
- `uv` for dependency management
- `fastapi` for the API
- `playwright` for browser automation
- `diskcache` for caching
- `trafilatura` for HTML extraction

## Notes

- The agent prefers `search_web` + `fetch_url` over `render_url`
- `render_url` is only used as a fallback for JavaScript-heavy pages
- All URLs are cached to reduce API calls and improve response times
- Page text is truncated before being sent to the LLM to stay within context limits
