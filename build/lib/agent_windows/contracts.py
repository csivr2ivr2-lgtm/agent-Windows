from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence


@dataclass(frozen=True)
class Message:
    role: str
    content: str


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMResponse:
    text: str = ""
    tool_calls: Sequence[ToolCall] = field(default_factory=tuple)
    provider: str = "unknown"


class LLMProvider(Protocol):
    name: str

    def is_available(self) -> bool: ...

    def complete(self, messages: Sequence[Message], tools: Sequence[Mapping[str, Any]]) -> LLMResponse: ...


class SpeechToText(Protocol):
    def transcribe(self, audio: bytes, *, language: str | None = None) -> str: ...


class TextToSpeech(Protocol):
    def synthesize(self, text: str, *, language: str | None = None) -> bytes: ...


class MemoryStore(Protocol):
    def search(self, query: str, *, limit: int = 5) -> Sequence[str]: ...

    def remember(self, text: str, *, metadata: Mapping[str, Any] | None = None) -> None: ...


class Tool(Protocol):
    name: str
    description: str
    schema: Mapping[str, Any]

    def invoke(self, arguments: Mapping[str, Any]) -> Any: ...

