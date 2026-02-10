"""FastAPI main application."""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from ..agent.agent_loop import AgentLoop
from ..logging import setup_logging, get_logger
from .models import BrowseRequest, BrowseResponse, HealthResponse

# Setup logging
setup_logging()
logger = get_logger(__name__)

# Create FastAPI app
app = FastAPI(
    title="AGI-1 Web Browsing Agent",
    description="Web browsing agent service using Brave Search, HTTP fetch, and Playwright",
    version="0.1.0",
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Agent will be initialized lazily on first request
_agent: AgentLoop | None = None


def get_agent() -> AgentLoop:
    """Get or create the agent instance."""
    global _agent
    if _agent is None:
        try:
            _agent = AgentLoop()
        except ValueError as e:
            # API key validation error
            raise HTTPException(
                status_code=500,
                detail=f"Configuration error: {str(e)}. Please check your .env file."
            )
    return _agent


@app.get("/health", response_model=HealthResponse)
async def health():
    """Health check endpoint."""
    return HealthResponse(status="ok")


@app.post("/browse", response_model=BrowseResponse)
async def browse(request: BrowseRequest):
    """
    Main endpoint for web browsing agent.

    Takes a user prompt and returns an answer with sources.
    """
    try:
        logger.info(f"Browse request: prompt='{request.prompt[:100]}...', mode={request.mode}, max_steps={request.max_steps}")

        # Get agent (will initialize on first call)
        agent = get_agent()

        # Run agent loop
        result = agent.run(
            user_prompt=request.prompt,
            max_steps=request.max_steps or 10,
        )

        # Convert sources to response format
        sources = [{"url": s["url"], "title": s.get("title", s["url"])} for s in result.get("sources", [])]

        return BrowseResponse(
            answer=result.get("answer", ""),
            sources=sources,
            debug=result.get("debug") if request.mode == "debug" else None,
        )

    except Exception as e:
        logger.error(f"Error in browse endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
