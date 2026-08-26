from __future__ import annotations

import json
from typing import Any, Iterator, Mapping, Sequence
from urllib.parse import quote

from ..contracts import LLMResponse, LLMStreamEvent, Message, ToolCall
from ..errors import ProviderBadResponse
from ..http import HTTPStatusError, HTTPTransport, UrllibTransport
from .base import validate_response


class GeminiProvider:
    name = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        transport: HTTPTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key.strip()
        self.model = self._normalize_model(model)
        self.transport = transport or UrllibTransport()
        self.timeout = timeout

    @staticmethod
    def _normalize_model(model: str) -> str:
        value = model.strip()
        for prefix in ("models/", "publishers/google/models/"):
            if value.startswith(prefix):
                value = value[len(prefix):]
        return value

    def is_available(self) -> bool:
        return bool(self.api_key and self.model)

    def _payload(
        self,
        messages: Sequence[Message],
        tools: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        contents = []
        system_parts = []
        for message in messages:
            if message.role == "system":
                system_parts.append({"text": message.content})
            elif message.role == "tool":
                contents.append({"role": "user", "parts": [{"text": message.content}]})
            else:
                role = "model" if message.role == "assistant" else "user"
                contents.append({"role": role, "parts": [{"text": message.content}]})
        payload: dict[str, Any] = {"contents": contents}
        if system_parts:
            payload["systemInstruction"] = {"parts": system_parts}
        if tools:
            payload["tools"] = [{"functionDeclarations": list(tools)}]
        return payload

    def _endpoint(self, method: str) -> str:
        model = quote(self.model, safe="")
        return f"https://generativelanguage.googleapis.com/v1beta/models/{model}:{method}"

    def complete(
        self,
        messages: Sequence[Message],
        tools: Sequence[Mapping[str, Any]],
    ) -> LLMResponse:
        response = self.transport.post_json(
            self._endpoint("generateContent"),
            {"x-goog-api-key": self.api_key},
            self._payload(messages, tools),
            self.timeout,
        )
        validate_response(response, self.name)
        try:
            parts = response.json()["candidates"][0]["content"]["parts"]
            text = "".join(part.get("text", "") for part in parts)
            calls = [
                ToolCall(part["functionCall"]["name"], part["functionCall"].get("args") or {})
                for part in parts
                if "functionCall" in part
            ]
            return LLMResponse(text, calls, self.name)
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ProviderBadResponse("gemini returned malformed JSON") from exc

    def stream_events(
        self,
        messages: Sequence[Message],
        tools: Sequence[Mapping[str, Any]],
        *,
        cancel_event=None,
    ) -> Iterator[LLMStreamEvent]:
        stream_sse = getattr(self.transport, "stream_sse", None)
        if stream_sse is None:
            response = self.complete(messages, tools)
            if response.text:
                yield LLMStreamEvent.text_delta(self.name, response.text)
            for call in response.tool_calls:
                yield LLMStreamEvent.call(self.name, call)
            return

        seen_calls: set[str] = set()
        try:
            for raw in stream_sse(
                self._endpoint("streamGenerateContent") + "?alt=sse",
                {"x-goog-api-key": self.api_key},
                self._payload(messages, tools),
                self.timeout,
            ):
                if cancel_event is not None and cancel_event.is_set():
                    return
                try:
                    data = json.loads(raw)
                    candidates = data.get("candidates") or []
                    if not candidates:
                        continue
                    parts = candidates[0].get("content", {}).get("parts", [])
                    for part in parts:
                        text = part.get("text")
                        if text:
                            yield LLMStreamEvent.text_delta(self.name, str(text))
                        raw_call = part.get("functionCall")
                        if raw_call:
                            name = str(raw_call.get("name") or "").strip()
                            arguments = raw_call.get("args") or {}
                            if not name or not isinstance(arguments, Mapping):
                                raise ProviderBadResponse("gemini streamed malformed functionCall")
                            identity = json.dumps(
                                {"name": name, "arguments": arguments},
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                                default=str,
                            )
                            if identity not in seen_calls:
                                seen_calls.add(identity)
                                yield LLMStreamEvent.call(
                                    self.name, ToolCall(name, dict(arguments))
                                )
                except ProviderBadResponse:
                    raise
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise ProviderBadResponse("gemini returned malformed streaming JSON") from exc
        except HTTPStatusError as exc:
            validate_response(exc.response, self.name)
            raise AssertionError("unreachable")

    def stream(
        self,
        messages: Sequence[Message],
        tools: Sequence[Mapping[str, Any]],
        *,
        cancel_event=None,
    ) -> Iterator[str]:
        for event in self.stream_events(messages, tools, cancel_event=cancel_event):
            if event.kind == "text" and event.text:
                yield event.text
