from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)
_MAX_RESPONSE = 4 * 1024 * 1024


class OpenVikingError(RuntimeError):
    pass


def _unwrap(payload: Any) -> Any:
    if isinstance(payload, dict) and "result" in payload and payload.get("status") in {"ok", "success"}:
        return payload["result"]
    return payload


@dataclass
class OpenVikingClient:
    base_url: str
    api_key: str = ""
    session_id: str = "ai-aharon"
    timeout: float = 8.0

    def __post_init__(self):
        self.base_url = self.base_url.rstrip("/")
        self.api_key = self.api_key.strip()
        self.session_id = self.session_id.strip() or "ai-aharon"
        self._session_ready = False
        self.last_error: str | None = None

    def is_configured(self) -> bool:
        return bool(self.base_url)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    def _post(self, path: str, payload: Mapping[str, Any] | None = None):
        if not self.is_configured():
            raise OpenVikingError("OpenViking is not configured")
        body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
        request = Request(
            self.base_url + path,
            data=body,
            headers=self._headers(),
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read(_MAX_RESPONSE + 1)
        except HTTPError as exc:
            detail = exc.read(2048).decode("utf-8", errors="replace")
            raise OpenVikingError(f"OpenViking HTTP {exc.code}: {detail[:300]}") from exc
        except (URLError, OSError, TimeoutError) as exc:
            raise OpenVikingError(f"OpenViking connection failed: {exc}") from exc
        if len(raw) > _MAX_RESPONSE:
            raise OpenVikingError("OpenViking response exceeds 4 MB")
        try:
            return _unwrap(json.loads(raw))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise OpenVikingError("OpenViking returned malformed JSON") from exc

    def ensure_session(self) -> None:
        if self._session_ready:
            return
        try:
            self._post("/api/v1/sessions", {"session_id": self.session_id})
        except OpenVikingError as exc:
            text = str(exc).casefold()
            if "409" not in text and "already" not in text and "exists" not in text:
                raise
        self._session_ready = True

    def remember(self, text: str) -> None:
        clean = text.strip()
        if not clean:
            return
        self.ensure_session()
        sid = quote(self.session_id, safe="")
        self._post(
            f"/api/v1/sessions/{sid}/messages",
            {"role": "user", "content": clean},
        )
        self._post(f"/api/v1/sessions/{sid}/commit", {"keep_recent_count": 2})
        self.last_error = None

    def find(self, query: str, *, limit: int = 5) -> list[str]:
        clean = query.strip()
        if not clean:
            return []
        result = self._post(
            "/api/v1/search/find",
            {
                "query": clean,
                "target_uri": "viking://user/memories/",
                "limit": max(1, min(20, int(limit))),
            },
        )
        resources = []
        if isinstance(result, dict):
            resources = result.get("resources") or result.get("items") or []
        elif isinstance(result, list):
            resources = result
        output: list[str] = []
        for item in resources:
            if isinstance(item, str):
                value = item
            elif isinstance(item, dict):
                value = str(
                    item.get("abstract")
                    or item.get("content")
                    or item.get("text")
                    or item.get("uri")
                    or ""
                )
            else:
                value = str(item)
            value = value.strip()
            if value and value not in output:
                output.append(value)
            if len(output) >= limit:
                break
        self.last_error = None
        return output

    def status(self) -> dict[str, Any]:
        return {
            "configured": self.is_configured(),
            "base_url": self.base_url,
            "session_id": self.session_id,
            "last_error": self.last_error,
        }


class TieredMemoryStore:
    """SQLite remains durable source-of-truth; OpenViking adds semantic retrieval when configured."""

    def __init__(self, primary, semantic: OpenVikingClient | None = None):
        self.primary = primary
        self.semantic = semantic

    def remember(self, text: str, *, metadata: Mapping[str, Any] | None = None) -> None:
        self.primary.remember(text, metadata=metadata)
        if not self.semantic or not self.semantic.is_configured():
            return
        try:
            self.semantic.remember(text)
        except OpenVikingError as exc:
            self.semantic.last_error = str(exc)
            logger.warning("OpenViking memory mirror deferred: %s", exc)

    def search(self, query: str, *, limit: int = 5) -> Sequence[str]:
        semantic_items: list[str] = []
        if self.semantic and self.semantic.is_configured():
            try:
                semantic_items = self.semantic.find(query, limit=limit)
            except OpenVikingError as exc:
                self.semantic.last_error = str(exc)
                logger.warning("OpenViking retrieval unavailable; using SQLite: %s", exc)
        local_items = list(self.primary.search(query, limit=limit))
        merged: list[str] = []
        for item in [*semantic_items, *local_items]:
            if item not in merged:
                merged.append(item)
            if len(merged) >= limit:
                break
        return merged

    def delete(self, memory_id: int | None = None) -> int:
        return self.primary.delete(memory_id)

    def status(self) -> dict[str, Any]:
        return {
            "primary": "sqlite",
            "semantic": self.semantic.status() if self.semantic else {"configured": False},
        }

    def close(self) -> None:
        self.primary.close()

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()
