"""Tests for final output sanitization and source generation in agent loop."""

from agi.agent.agent_loop import AgentLoop


class DummyOpenRouterClient:
    """Deterministic two-step LLM stub."""

    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools=None):  # noqa: ARG002
        self.calls += 1
        if self.calls == 1:
            return {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "fetch_url",
                            "arguments": '{"url":"https://shop.example.com/product/ring-123"}',
                        },
                    }
                ],
            }

        return {
            "role": "assistant",
            "content": (
                "Found a good match: "
                "https://shop.example.com/collections/rings "
                "with similar attributes."
            ),
        }


class DummyBraveClient:
    def search(self, query, count=5):  # noqa: ARG002
        return []


class DummyFetchClient:
    def fetch(self, url):
        return {
            "status": 200,
            "final_url": url,
            "canonical_url": url,
            "html": (
                '<html><body><h1>Ring 123</h1>'
                '<script type="application/ld+json">'
                '{"@type":"Product","name":"Ring 123"}'
                "</script></body></html>"
            ),
            "text": "Ring 123",
            "classification": {
                "verdict": "product",
                "reason": "Detected product schema",
                "product_count": 1,
            },
        }


class DummyBrowserClient:
    def render(self, url):  # noqa: ARG002
        return {}


class DummyCache:
    def get_search(self, query):  # noqa: ARG002
        return None

    def set_search(self, query, results):  # noqa: ARG002
        return None

    def get_fetch(self, url):  # noqa: ARG002
        return None

    def set_fetch(self, url, data):  # noqa: ARG002
        return None

    def get_render(self, url):  # noqa: ARG002
        return None

    def set_render(self, url, data):  # noqa: ARG002
        return None


def test_run_sanitizes_final_answer_and_returns_verified_sources():
    """Final answer should include only verified URLs and sources should be derived from verified URLs."""
    agent = AgentLoop(
        openrouter_client=DummyOpenRouterClient(),
        brave_client=DummyBraveClient(),
        fetch_client=DummyFetchClient(),
        browser_client=DummyBrowserClient(),
        cache=DummyCache(),
    )

    result = agent.run("Find similar rings", max_steps=3, max_pages_fetched=5)

    verified_url = "https://shop.example.com/product/ring-123"
    assert verified_url in result["answer"]
    assert "https://shop.example.com/collections/rings" not in result["answer"]

    assert len(result["sources"]) == 1
    assert result["sources"][0]["url"] == verified_url
