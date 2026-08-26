import json
import threading
import unittest

from agent_windows.contracts import Message
from agent_windows.errors import ProviderPermissionError
from agent_windows.http import HTTPResponse, HTTPStatusError
from agent_windows.providers.base import OpenAICompatibleProvider
from agent_windows.providers.gemini import GeminiProvider


class StreamTransport:
    def __init__(self, chunks=(), status_error=None):
        self.chunks = list(chunks)
        self.status_error = status_error
        self.calls = []

    def stream_sse(self, url, headers, payload, timeout):
        self.calls.append((url, headers, payload, timeout))
        if self.status_error is not None:
            raise self.status_error
        yield from self.chunks

    def post_json(self, url, headers, payload, timeout):
        self.calls.append((url, headers, payload, timeout))
        return HTTPResponse(
            200,
            json.dumps({"choices": [{"message": {"content": "fallback"}}]}).encode(),
            {},
        )


class StreamingLLMTests(unittest.TestCase):
    def test_openai_compatible_stream_yields_deltas(self):
        transport = StreamTransport([
            json.dumps({"choices": [{"delta": {"content": "של"}}]}).encode(),
            json.dumps({"choices": [{"delta": {"content": "ום"}}]}).encode(),
            b"[DONE]",
        ])
        provider = OpenAICompatibleProvider(
            api_key="k",
            model="m",
            endpoint="https://example.test/v1/chat/completions",
            transport=transport,
        )
        self.assertEqual(
            list(provider.stream([Message("user", "היי")], [])),
            ["של", "ום"],
        )
        self.assertTrue(transport.calls[0][2]["stream"])

    def test_stream_honors_cancellation(self):
        transport = StreamTransport([
            json.dumps({"choices": [{"delta": {"content": "one"}}]}).encode(),
            json.dumps({"choices": [{"delta": {"content": "two"}}]}).encode(),
        ])
        provider = OpenAICompatibleProvider(
            api_key="k", model="m", endpoint="https://example.test", transport=transport
        )
        event = threading.Event()
        stream = provider.stream([Message("user", "x")], [], cancel_event=event)
        self.assertEqual(next(stream), "one")
        event.set()
        self.assertEqual(list(stream), [])

    def test_stream_preserves_http_403_classification(self):
        response = HTTPResponse(403, b"{}", {})
        transport = StreamTransport(status_error=HTTPStatusError(response))
        provider = OpenAICompatibleProvider(
            api_key="k", model="m", endpoint="https://example.test", transport=transport
        )
        with self.assertRaises(ProviderPermissionError):
            list(provider.stream([Message("user", "x")], []))

    def test_gemini_stream_uses_stream_generate_content(self):
        transport = StreamTransport([
            json.dumps({
                "candidates": [{"content": {"parts": [{"text": "שלום"}]}}]
            }).encode()
        ])
        provider = GeminiProvider(
            api_key="g",
            model="models/gemini-2.5-flash",
            transport=transport,
        )
        self.assertEqual(list(provider.stream([Message("user", "x")], [])), ["שלום"])
        url = transport.calls[0][0]
        self.assertIn("gemini-2.5-flash:streamGenerateContent?alt=sse", url)
        self.assertNotIn("models%2F", url)

    def test_tools_use_nonstreaming_path_until_tool_stream_contract_exists(self):
        transport = StreamTransport()
        provider = OpenAICompatibleProvider(
            api_key="k", model="m", endpoint="https://example.test", transport=transport
        )
        chunks = list(provider.stream(
            [Message("user", "x")],
            [{"name": "clock", "description": "clock", "parameters": {}}],
        ))
        self.assertEqual(chunks, ["fallback"])
        self.assertNotIn("stream", transport.calls[0][2])


if __name__ == "__main__":
    unittest.main()
