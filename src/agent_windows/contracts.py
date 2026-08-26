from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Protocol, Sequence


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


@dataclass(frozen=True)
class LLMStreamEvent:
    """Provider-neutral event used by the realtime agent loop.

    Providers may emit text deltas and completed tool calls in the same streamed turn.
    Tool arguments are only exposed after the provider's JSON fragments have been fully
    assembled and validated.
    """

    kind: Literal["text", "tool_call"]
    provider: str
    text: str = ""
    tool_call: ToolCall | None = None

    @classmethod
    def text_delta(cls, provider: str, text: str) -> "LLMStreamEvent":
        return cls("text", provider, text=text)

    @classmethod
    def call(cls, provider: str, tool_call: ToolCall) -> "LLMStreamEvent":
        return cls("tool_call", provider, tool_call=tool_call)


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
