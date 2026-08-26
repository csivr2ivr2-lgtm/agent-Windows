import json
import unittest

from agent_windows.contracts import Message
from agent_windows.errors import (
    ProviderAuthenticationError,
    ProviderBadResponse,
    ProviderConnectionError,
    ProviderRateLimited,
    ProviderServerError,
    ProviderTimeout,
)
from agent_windows.http import HTTPResponse
from agent_windows.providers import GeminiProvider, GroqProvider, OpenRouterProvider


class MockTransport:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def post_json(self, url, headers, payload, timeout):
        self.calls.append((url, headers, payload, timeout))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def response(status, body=None, headers=None):
    return HTTPResponse(status, json.dumps(body or {}).encode(), headers or {})


class ProviderAdapterTests(unittest.TestCase):
    def test_groq_parses_text_and_function_call(self):
        transport = MockTransport(response(200, {"choices": [{"message": {
            "content": "working",
            "tool_calls": [{"function": {"name": "echo", "arguments": "{\"text\":\"ok\"}"}}],
        }}]}))
        provider = GroqProvider(api_key="test", model="model", transport=transport, timeout=7)
        result = provider.complete([Message("user", "hi")], [{"name": "echo"}])
        self.assertEqual(result.provider, "groq")
        self.assertEqual(result.tool_calls[0].arguments["text"], "ok")
        self.assertEqual(transport.calls[0][3], 7)

    def test_openrouter_uses_its_endpoint_and_title(self):
        transport = MockTransport(response(200, {"choices": [{"message": {"content": "ok"}}]}))
        provider = OpenRouterProvider(api_key="test", model="free-model", transport=transport)
        provider.complete([Message("user", "hi")], [])
        url, headers, _, _ = transport.calls[0]
        self.assertEqual(url, "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(headers["X-Title"], "agent-Windows")

    def test_gemini_parses_text_and_function_call(self):
        transport = MockTransport(response(200, {"candidates": [{"content": {"parts": [
            {"text": "working"}, {"functionCall": {"name": "echo", "args": {"text": "ok"}}}
        ]}}]}))
        provider = GeminiProvider(api_key="test", model="model", transport=transport)
        result = provider.complete([Message("system", "safe"), Message("user", "hi")], [{"name": "echo"}])
        self.assertEqual(result.provider, "gemini")
        self.assertEqual(result.text, "working")
        self.assertEqual(result.tool_calls[0].name, "echo")
        self.assertIn("x-goog-api-key", transport.calls[0][1])

    def test_401_and_403_are_authentication_errors(self):
        for status in (401, 403):
            with self.subTest(status=status):
                provider = GroqProvider(api_key="bad", model="model", transport=MockTransport(response(status)))
                with self.assertRaises(ProviderAuthenticationError):
                    provider.complete([Message("user", "hi")], [])

    def test_429_captures_retry_after(self):
        provider = GroqProvider(
            api_key="test", model="model", transport=MockTransport(response(429, headers={"Retry-After": "12"}))
        )
        with self.assertRaises(ProviderRateLimited) as caught:
            provider.complete([Message("user", "hi")], [])
        self.assertEqual(caught.exception.retry_after, 12)

    def test_5xx_is_server_error(self):
        provider = GeminiProvider(api_key="test", model="model", transport=MockTransport(response(503)))
        with self.assertRaises(ProviderServerError):
            provider.complete([Message("user", "hi")], [])

    def test_timeout_and_connection_errors_are_preserved(self):
        for error in (ProviderTimeout("slow"), ProviderConnectionError("offline")):
            with self.subTest(error=type(error).__name__):
                provider = GroqProvider(api_key="test", model="model", transport=MockTransport(error))
                with self.assertRaises(type(error)):
                    provider.complete([Message("user", "hi")], [])

    def test_malformed_success_is_bad_response(self):
        provider = OpenRouterProvider(api_key="test", model="model", transport=MockTransport(response(200, {"no": "choices"})))
        with self.assertRaises(ProviderBadResponse):
            provider.complete([Message("user", "hi")], [])


if __name__ == "__main__":
    unittest.main()


def test_deprecated_provider_models_are_migrated():
    groq = GroqProvider(api_key="test", model="llama-3.1-8b-instant", transport=MockTransport(response(200)))
    gemini = GeminiProvider(api_key="test", model="gemini-2.0-flash", transport=MockTransport(response(200)))
    assert groq.model == "openai/gpt-oss-20b"
    assert gemini.model == "gemini-3.7-flash"
