import json
import unittest
from types import SimpleNamespace
from unittest import mock
from urllib.error import HTTPError, URLError

from agent_windows.web_tools import (
    FirecrawlAdapter,
    JSONTransport,
    WebIntegrationError,
    WebRouter,
    WigoloAdapter,
    build_web_tools,
)


class FakeTransport:
    def __init__(self, results=(), error=None):
        self.results = list(results)
        self.error = error
        self.calls = []
    def post(self, url, payload, *, headers=None, timeout=20.0):
        self.calls.append((url, payload, dict(headers or {}), timeout))
        if self.error: raise self.error
        return self.results.pop(0) if self.results else {"ok": True}


class WebToolsTests(unittest.TestCase):
    def test_wigolo_paths_token_and_bounds(self):
        transport = FakeTransport([{"s": 1}, {"f": 2}, {"r": 3}])
        wigolo = WigoloAdapter("http://127.0.0.1:3333/", "secret", transport=transport)
        self.assertEqual(wigolo.search("q", max_results=99), {"s": 1})
        self.assertEqual(wigolo.fetch("https://example.com", max_content_chars=5), {"f": 2})
        self.assertEqual(wigolo.research("q", depth="quick", max_sources=99), {"r": 3})
        self.assertEqual(transport.calls[0][0], "http://127.0.0.1:3333/v1/search")
        self.assertEqual(transport.calls[0][1]["max_results"], 20)
        self.assertEqual(transport.calls[0][2]["Authorization"], "Bearer secret")
        with self.assertRaises(ValueError): wigolo.research("q", depth="bad")

    def test_firecrawl_search_fetch_research_and_config(self):
        long = "x" * 5000
        transport = FakeTransport([
            {"data": [1]},
            {"data": {"markdown": long}},
            {"data": ["evidence"]},
        ])
        firecrawl = FirecrawlAdapter("fc-key", transport=transport)
        self.assertTrue(firecrawl.is_configured())
        firecrawl.search("q", max_results=50)
        fetched = firecrawl.fetch("https://example.com", max_content_chars=1200)
        self.assertEqual(len(fetched["data"]["markdown"]), 1200)
        research = firecrawl.research("question", depth="standard", max_sources=7)
        self.assertEqual(research["mode"], "search_evidence")
        self.assertEqual(transport.calls[0][0], "https://api.firecrawl.dev/v2/search")
        self.assertEqual(transport.calls[0][2]["Authorization"], "Bearer fc-key")
        self.assertEqual(transport.calls[2][1]["limit"], 7)
        with self.assertRaises(ValueError): firecrawl.research("x", depth="bad")
        with self.assertRaises(WebIntegrationError): FirecrawlAdapter("").search("q")

    def test_router_prefers_wigolo_then_falls_back_firecrawl(self):
        wigolo = WigoloAdapter(transport=FakeTransport(error=WebIntegrationError("down")))
        fire = FirecrawlAdapter("key", transport=FakeTransport([{"data": [1]}]))
        router = WebRouter(wigolo, fire)
        result = router.search("hello")
        self.assertEqual(result["provider"], "firecrawl")
        self.assertEqual(router.status()["firecrawl"]["configured"], True)
        router = WebRouter(WigoloAdapter("", transport=FakeTransport()), FirecrawlAdapter("", transport=FakeTransport()))
        with self.assertRaisesRegex(WebIntegrationError, "no configured"):
            router.fetch("https://example.com")

    def test_tool_registry_functions_are_read_only_and_callable(self):
        router = SimpleNamespace(
            search=mock.Mock(return_value={"x": 1}),
            fetch=mock.Mock(return_value={"y": 2}),
            research=mock.Mock(return_value={"z": 3}),
        )
        tools = {tool.name: tool for tool in build_web_tools(router)}
        self.assertEqual(set(tools), {"web_search", "web_fetch", "web_research"})
        self.assertTrue(all(tool.risk == "read_only" for tool in tools.values()))
        tools["web_search"].invoke({"query": "q", "max_results": 3})
        tools["web_fetch"].invoke({"url": "https://x", "max_content_chars": 2000})
        tools["web_research"].invoke({"question": "q", "depth": "quick", "max_sources": 4})
        router.search.assert_called_once_with("q", max_results=3)
        router.fetch.assert_called_once_with("https://x", max_content_chars=2000)
        router.research.assert_called_once_with("q", depth="quick", max_sources=4)

    def test_json_transport_success_bad_json_and_errors(self):
        class Resp:
            def __init__(self, raw): self.raw = raw
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, _limit): return self.raw
        transport = JSONTransport()
        with mock.patch("agent_windows.web_tools.urlopen", return_value=Resp(b'{"ok":true}')):
            self.assertEqual(transport.post("https://x", {"q": 1}), {"ok": True})
        with mock.patch("agent_windows.web_tools.urlopen", return_value=Resp(b"bad")):
            with self.assertRaisesRegex(WebIntegrationError, "malformed"):
                transport.post("https://x", {})
        with mock.patch("agent_windows.web_tools.urlopen", side_effect=URLError("down")):
            with self.assertRaisesRegex(WebIntegrationError, "connection"):
                transport.post("https://x", {})
        exc = HTTPError("https://x", 429, "rate", {}, mock.Mock(read=lambda n: b"limited"))
        with mock.patch("agent_windows.web_tools.urlopen", side_effect=exc):
            with self.assertRaisesRegex(WebIntegrationError, "HTTP 429"):
                transport.post("https://x", {})
        with self.assertRaisesRegex(WebIntegrationError, "request exceeds"):
            transport.post("https://x", {"q": "x" * (1024 * 1024 + 1)})


if __name__ == "__main__":
    unittest.main()
