"""OpenRouter API client for chat completions."""

import httpx
from typing import Any, Dict, List, Optional

from ..config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, OR_MODEL
from ..logging import get_logger

logger = get_logger(__name__)


class OpenRouterClient:
    """Client for OpenRouter chat completions API."""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or OPENROUTER_API_KEY
        self.model = model or OR_MODEL
        self.base_url = OPENROUTER_BASE_URL
        
        # Validate API key
        if not self.api_key or not self.api_key.strip():
            raise ValueError(
                "OPENROUTER_API_KEY is not set. Please set it in your .env file or environment variables."
            )
        
        self.client = httpx.Client(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.api_key.strip()}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/your-repo",  # Optional but recommended by OpenRouter
                "X-Title": "AGI-1 Web Browsing Agent",  # Optional
            },
            timeout=60.0,
        )

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
    ) -> Dict[str, Any]:
        """
        Send chat completion request.

        Args:
            messages: List of message dicts with 'role' and 'content'
            tools: Optional list of tool definitions
            temperature: Sampling temperature

        Returns:
            Raw assistant message object from API response
        """
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }

        if tools:
            payload["tools"] = tools

        try:
            response = self.client.post("/chat/completions", json=payload)
            response.raise_for_status()
            data = response.json()

            # Extract the assistant message
            if "choices" in data and len(data["choices"]) > 0:
                choice = data["choices"][0]
                message = choice.get("message", {})
                logger.debug(f"OpenRouter response: {message.get('role')} message with {len(message.get('tool_calls', []))} tool calls")
                return message
            else:
                raise ValueError("No choices in OpenRouter response")

        except httpx.HTTPStatusError as e:
            error_text = e.response.text
            try:
                error_json = e.response.json()
                error_text = str(error_json)
                logger.error(f"OpenRouter API error: {e.response.status_code} - {error_json}")
            except:
                logger.error(f"OpenRouter API error: {e.response.status_code} - {error_text}")
            # Log the request payload for debugging
            logger.error(f"Model: {self.model}")
            logger.error(f"Has tools: {bool(tools)}")
            if tools:
                logger.error(f"Number of tools: {len(tools)}")
            raise ValueError(f"OpenRouter API error ({e.response.status_code}): {error_text}")
        except Exception as e:
            logger.error(f"OpenRouter request failed: {e}")
            raise

    def __del__(self):
        """Close HTTP client on cleanup."""
        if hasattr(self, "client"):
            self.client.close()
