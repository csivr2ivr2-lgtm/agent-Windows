from __future__ import annotations

import json
from typing import Any, Mapping, Sequence
from urllib.parse import quote

from ..contracts import LLMResponse, Message, ToolCall
from ..errors import ProviderBadResponse
from ..http import HTTPTransport, UrllibTransport
from .base import validate_response


_DEPRECATED_GEMINI_MODELS = {
    "gemini-2.0-flash": "gemini-3.7-flash",
    "gemini-2.0-flash-lite": "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite-preview": "gemini-3.1-flash-lite",
    "gemini-3-pro-preview": "gemini-3.1-pro-preview",
}


class GeminiProvider:
    name = "gemini"

    def __init__(self, *, api_key: str, model: str, transport: HTTPTransport | None = None, timeout: float = 30.0) -> None:
        self.api_key = api_key.strip()
        requested = model.strip()
        self.model = _DEPRECATED_GEMINI_MODELS.get(requested, requested or "gemini-3.7-flash")
        self.transport = transport or UrllibTransport()
        self.timeout = timeout

    def is_available(self) -> bool:
        return bool(self.api_key and self.model)

    def complete(self, messages: Sequence[Message], tools: Sequence[Mapping[str, Any]]) -> LLMResponse:
        contents = []
        system_parts = []
        for message in messages:
            if message.role == "system":
                system_parts.append({"text": message.content})
            else:
                role = "model" if message.role == "assistant" else "user"
                contents.append({"role": role, "parts": [{"text": message.content}]})
        payload: dict[str, Any] = {"contents": contents}
        if system_parts:
            payload["systemInstruction"] = {"parts": system_parts}
        if tools:
            payload["tools"] = [{"functionDeclarations": list(tools)}]
        endpoint = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{quote(self.model, safe='')}:generateContent"
        )
        response = self.transport.post_json(
            endpoint, {"x-goog-api-key": self.api_key}, payload, self.timeout
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
