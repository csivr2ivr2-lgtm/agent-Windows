from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .contracts import Message


@dataclass(frozen=True)
class OptimizedRequest:
    """Compatibility result for the original optimizer API."""

    messages: list[Message]
    tools: dict[str, Mapping[str, Any]]


class RequestOptimizer:
    def __init__(self, max_messages: int | None = None, max_chars: int | None = None) -> None:
        self.max_messages = max_messages
        self.max_chars = max_chars

    def optimize(
        self,
        messages: Sequence[Message],
        tools: Sequence[Mapping[str, Any]] | Mapping[str, Mapping[str, Any]],
        *,
        max_chars: int | None = None,
        max_tools: int | None = None,
        allowed_tools: set[str] | None = None,
    ):
        legacy = isinstance(tools, Mapping) or allowed_tools is not None
        char_limit = max_chars if max_chars is not None else self.max_chars
        if char_limit is None:
            raise TypeError("max_chars is required")

        deduped = []
        for message in messages:
            clean = " ".join(message.content.split())
            if clean and (not deduped or (deduped[-1].role, deduped[-1].content) != (message.role, clean)):
                deduped.append(Message(message.role, clean))

        total = 0
        kept = []
        for message in reversed(deduped):
            remaining = char_limit - total
            if remaining <= 0:
                break
            content = message.content[-remaining:]
            kept.append(Message(message.role, content))
            total += len(content)
        kept.reverse()
        if self.max_messages is not None:
            kept = kept[-self.max_messages :]

        omitted = deduped[:max(0, len(deduped) - len(kept))]
        if omitted and char_limit > 120:
            summary = "Earlier context: " + " | ".join(f"{m.role}: {m.content[:80]}" for m in omitted[-3:])
            room = max(0, char_limit - sum(len(m.content) for m in kept))
            if room:
                kept.insert(0, Message("system", summary[:room]))

        if isinstance(tools, Mapping):
            named_tools = [{"name": name, **dict(schema)} for name, schema in tools.items()]
        else:
            named_tools = [dict(tool) for tool in tools]
        if allowed_tools is not None:
            named_tools = [tool for tool in named_tools if str(tool.get("name", "")) in allowed_tools]

        query = " ".join(m.content.casefold() for m in kept[-2:])
        ranked = sorted(
            named_tools,
            key=lambda tool: (
                -sum(word in query for word in str(tool.get("name", "")).casefold().replace("_", " ").split()),
                str(tool.get("name", "")),
            ),
        )
        tool_limit = max_tools if max_tools is not None else len(ranked)
        selected = ranked[:tool_limit]
        if legacy:
            return OptimizedRequest(
                kept,
                {str(tool["name"]): {key: value for key, value in tool.items() if key != "name"} for tool in selected},
            )
        return kept, selected
