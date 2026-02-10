"""API request/response models."""

from typing import List, Optional
from pydantic import BaseModel, Field


class BrowseRequest(BaseModel):
    """Request model for /browse endpoint."""

    prompt: str = Field(..., description="User prompt/query")
    mode: Optional[str] = Field(
        default="auto",
        description="Mode: 'auto' (search+fetch, fallback to render), 'search', or 'browser'",
    )
    max_steps: Optional[int] = Field(
        default=10,
        ge=1,
        le=20,
        description="Maximum number of agent steps",
    )


class Source(BaseModel):
    """Source URL information."""

    url: str
    title: str


class BrowseResponse(BaseModel):
    """Response model for /browse endpoint."""

    answer: str = Field(..., description="Final answer from agent")
    sources: List[Source] = Field(default_factory=list, description="List of source URLs")
    debug: Optional[dict] = Field(default=None, description="Debug information (tool traces)")


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = "ok"
