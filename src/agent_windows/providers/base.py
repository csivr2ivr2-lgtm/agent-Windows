from __future__ import annotations

import json
from typing import Any, Iterator, Mapping, Sequence

from ..contracts import LLMResponse, Message, ToolCall
from ..errors import (
    ProviderAuthenticationError,
    ProviderBadResponse,
    ProviderPermissionError,
    ProviderRateLimited,
    ProviderServerError,
)
from ..http import HTTPResponse, HTTPStatusError, HTTPTransport, UrllibTransport


def _retry_after(response: HTTPResponse) -> float | None:
    value = next((v for k, v in response.headers.items() if k.casefold() == "retry-after"), None)
    try:
        return max(0.0, float(value)) if value is not None else None
    except ValueError:
        return None


def validate_response(response: HTTPResponse, provider: str) -> None:
    if response.status == 401:
        raise ProviderAuthenticationError(f"{provider} authentication failed (HTTP 401)")
    if response.status == 403:
        raise ProviderPermissionError(
            f"{provider} permission/model access denied (HTTP 403)"
        )
    if response.status == 429:
        raise ProviderRateLimited(
            f"{provider} rate limited the request",
            retry_after=_retry_after(response),
        )
    if 500 <= response.status <= 599:
        raise ProviderServerError(f"{provider} server error (HTTP {response.status})")
    if not 200 <= response.status <= 299:
        raise ProviderBadResponse(f"{provider} returned HTTP {response.status}")


class OpenAICompatibleProvider:
    name = "openai-compatible"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        endpoint: str,
        transport: HTTPTransport | None = None,
        timeout: float = 30.0,
        extra_headers: Mapping[str, str] | None = None,
    ) -> None:
        self.api_key = api_key.strip()
        self.model = model.strip()
        self.endpoint = endpoint
        self.transport = transport or UrllibTransport()
        self.timeout = timeout
        self.extra_headers = dict(extra_headers or {})

    def is_available(self) -> bool:
        return bool(self.api_key and self.model)

    def _payload(
        self,
        messages: Sequence[Message],
        tools: Sequence[Mapping[str, Any]],
        *,
        stream: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        if tools:
            payload["tools"] = [{"type": "function", "function": tool} for tool in tools]
        if stream:
            payload["stream"] = True
        return payload

    def complete(
        self,
        messages: Sequence[Message],
        tools: Sequence[Mapping[str, Any]],
    ) -> LLMResponse:
        response = self.transport.post_json(
            self.endpoint,
            {"Authorization": f"Bearer {self.api_key}", **self.extra_headers},
            self._payload(messages, tools),
            self.timeout,
        )
        validate_response(response, self.name)
        try:
            message = response.json()["choices"][0]["message"]
            calls = []
            for raw in message.get("tool_calls") or []:
                function = raw["function"]
                arguments = function.get("arguments") or {}
                if isinstance(arguments, str):
                    arguments = json.loads(arguments)
                if not isinstance(arguments, Mapping):
                    raise TypeError("tool arguments must be an object")
                calls.append(ToolCall(function["name"], arguments))
            return LLMResponse(message.get("content") or "", calls, self.name)
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ProviderBadResponse(f"{self.name} returned malformed JSON") from exc

    def stream(
        self,
        messages: Sequence[Message],
        tools: Sequence[Mapping[str, Any]],
        *,
        cancel_event=None,
    ) -> Iterator[str]:
        """Stream text deltas.

        Tool-call turns intentionally use the regular completion path until a unified
        tool-call stream event contract is active, so tool semantics aren't silently lost.
        """
        stream_sse = getattr(self.transport, "stream_sse", None)
        if tools or stream_sse is None:
            response = self.complete(messages, tools)
            if response.text:
                yield response.text
            return

        try:
            data_stream = stream_sse(
                self.endpoint,
                {"Authorization": f"Bearer {self.api_key}", **self.extra_headers},
                self._payload(messages, tools, stream=True),
                self.timeout,
            )
            for raw in data_stream:
                if cancel_event is not None and cancel_event.is_set():
                    return
                if raw == b"[DONE]":
                    return
                try:
                    data = json.loads(raw)
                    choices = data.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    text = delta.get("content")
                    if text:
                        yield str(text)
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise ProviderBadResponse(
                        f"{self.name} returned malformed streaming JSON"
                    ) from exc
        except HTTPStatusError as exc:
            validate_response(exc.response, self.name)
            raise AssertionError("unreachable")
