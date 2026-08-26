from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .windows_tools import FunctionTool

_MAX_RESPONSE = 8 * 1024 * 1024


class WebIntegrationError(RuntimeError):
    pass


class JSONTransport:
    def post(self, url: str, payload: Mapping[str, Any], *, headers: Mapping[str, str] | None = None, timeout: float = 20.0):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if len(body) > 1024 * 1024:
            raise WebIntegrationError("web tool request exceeds 1 MB")
        req = Request(
            url,
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json", **dict(headers or {})},
            method="POST",
        )
        try:
            with urlopen(req, timeout=timeout) as response:
                raw = response.read(_MAX_RESPONSE + 1)
        except HTTPError as exc:
            detail = exc.read(4096).decode("utf-8", errors="replace")
            raise WebIntegrationError(f"web integration HTTP {exc.code}: {detail[:500]}") from exc
        except (URLError, OSError, TimeoutError) as exc:
            raise WebIntegrationError(f"web integration connection failed: {exc}") from exc
        if len(raw) > _MAX_RESPONSE:
            raise WebIntegrationError("web integration response exceeds 8 MB")
        try:
            return json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise WebIntegrationError("web integration returned malformed JSON") from exc


@dataclass
class WigoloAdapter:
    base_url: str = "http://127.0.0.1:3333"
    token: str = ""
    timeout: float = 20.0
    transport: JSONTransport | None = None

    name = "wigolo"

    def __post_init__(self):
        self.base_url = self.base_url.rstrip("/")
        self.transport = self.transport or JSONTransport()

    def is_configured(self) -> bool:
        return bool(self.base_url)

    def _call(self, tool: str, payload: Mapping[str, Any]):
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        return self.transport.post(
            f"{self.base_url}/v1/{tool}", payload, headers=headers, timeout=self.timeout
        )

    def search(self, query: str, *, max_results: int = 5):
        return self._call("search", {"query": query, "max_results": max(1, min(20, int(max_results)))})

    def fetch(self, url: str, *, max_content_chars: int = 30000):
        return self._call("fetch", {"url": url, "max_content_chars": max(1000, min(100000, int(max_content_chars)))})

    def research(self, question: str, *, depth: str = "standard", max_sources: int = 12):
        if depth not in {"quick", "standard", "comprehensive"}:
            raise ValueError("depth must be quick, standard, or comprehensive")
        return self._call("research", {"question": question, "depth": depth, "max_sources": max(1, min(50, int(max_sources)))})


@dataclass
class FirecrawlAdapter:
    api_key: str
    base_url: str = "https://api.firecrawl.dev"
    timeout: float = 25.0
    transport: JSONTransport | None = None

    name = "firecrawl"

    def __post_init__(self):
        self.api_key = self.api_key.strip()
        self.base_url = self.base_url.rstrip("/")
        self.transport = self.transport or JSONTransport()

    def is_configured(self) -> bool:
        return bool(self.api_key and self.base_url)

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def _require(self):
        if not self.is_configured():
            raise WebIntegrationError("Firecrawl is not configured")

    def search(self, query: str, *, max_results: int = 5):
        self._require()
        return self.transport.post(
            f"{self.base_url}/v2/search",
            {
                "query": query,
                "limit": max(1, min(20, int(max_results))),
                "scrapeOptions": {"formats": ["markdown"]},
            },
            headers=self._headers,
            timeout=self.timeout,
        )

    def fetch(self, url: str, *, max_content_chars: int = 30000):
        self._require()
        result = self.transport.post(
            f"{self.base_url}/v2/scrape",
            {"url": url, "formats": ["markdown"]},
            headers=self._headers,
            timeout=self.timeout,
        )
        if isinstance(result, dict):
            data = result.get("data")
            if isinstance(data, dict) and isinstance(data.get("markdown"), str):
                copy = dict(result)
                copy_data = dict(data)
                copy_data["markdown"] = copy_data["markdown"][: max(1000, min(100000, int(max_content_chars)))]
                copy["data"] = copy_data
                return copy
        return result

    def research(self, question: str, *, depth: str = "standard", max_sources: int = 12):
        limit = {"quick": 5, "standard": 10, "comprehensive": 20}.get(depth)
        if limit is None:
            raise ValueError("depth must be quick, standard, or comprehensive")
        return {
            "provider": self.name,
            "mode": "search_evidence",
            "question": question,
            "evidence": self.search(question, max_results=min(limit, max_sources)),
        }


class WebRouter:
    def __init__(self, wigolo: WigoloAdapter, firecrawl: FirecrawlAdapter):
        self.wigolo = wigolo
        self.firecrawl = firecrawl

    def _run(self, method: str, *args, **kwargs):
        failures: list[str] = []
        for provider in (self.wigolo, self.firecrawl):
            if not provider.is_configured():
                continue
            try:
                result = getattr(provider, method)(*args, **kwargs)
                return {"provider": provider.name, "result": result}
            except (WebIntegrationError, OSError) as exc:
                failures.append(f"{provider.name}: {exc}")
        detail = "; ".join(failures) if failures else "no configured web provider"
        raise WebIntegrationError("All web providers failed: " + detail)

    def search(self, query: str, *, max_results: int = 5):
        return self._run("search", query, max_results=max_results)

    def fetch(self, url: str, *, max_content_chars: int = 30000):
        return self._run("fetch", url, max_content_chars=max_content_chars)

    def research(self, question: str, *, depth: str = "standard", max_sources: int = 12):
        return self._run("research", question, depth=depth, max_sources=max_sources)

    def status(self) -> dict[str, dict[str, Any]]:
        return {
            "wigolo": {"configured": self.wigolo.is_configured(), "base_url": self.wigolo.base_url},
            "firecrawl": {"configured": self.firecrawl.is_configured(), "base_url": self.firecrawl.base_url},
        }


def build_web_tools(router: WebRouter) -> list[FunctionTool]:
    def search(args):
        return router.search(str(args["query"]), max_results=int(args.get("max_results", 5)))

    def fetch(args):
        return router.fetch(str(args["url"]), max_content_chars=int(args.get("max_content_chars", 30000)))

    def research(args):
        return router.research(
            str(args["question"]),
            depth=str(args.get("depth", "standard")),
            max_sources=int(args.get("max_sources", 12)),
        )

    return [
        FunctionTool(
            "web_search",
            "Search the live web through local Wigolo first, then Firecrawl fallback",
            {"type": "object", "properties": {"query": {"type": "string"}, "max_results": {"type": "integer"}}, "required": ["query"]},
            search,
            risk="read_only",
        ),
        FunctionTool(
            "web_fetch",
            "Fetch a URL as structured clean web content",
            {"type": "object", "properties": {"url": {"type": "string"}, "max_content_chars": {"type": "integer"}}, "required": ["url"]},
            fetch,
            risk="read_only",
        ),
        FunctionTool(
            "web_research",
            "Collect multi-source web evidence for a research question",
            {"type": "object", "properties": {"question": {"type": "string"}, "depth": {"type": "string", "enum": ["quick", "standard", "comprehensive"]}, "max_sources": {"type": "integer"}}, "required": ["question"]},
            research,
            risk="read_only",
        ),
    ]
