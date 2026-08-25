from __future__ import annotations

from typing import Any, Mapping, Sequence

from .contracts import Message


class RequestOptimizer:
    def optimize(self, messages: Sequence[Message], tools: Sequence[Mapping[str, Any]], *, max_chars: int, max_tools: int):
        deduped = []
        for message in messages:
            clean = " ".join(message.content.split())
            if clean and (not deduped or (deduped[-1].role, deduped[-1].content) != (message.role, clean)):
                deduped.append(Message(message.role, clean))
        total = 0
        kept = []
        for message in reversed(deduped):
            remaining = max_chars - total
            if remaining <= 0:
                break
            content = message.content[-remaining:]
            kept.append(Message(message.role, content))
            total += len(content)
        kept.reverse()
        omitted = deduped[:max(0, len(deduped)-len(kept))]
        if omitted and max_chars > 120:
            summary = "Earlier context: " + " | ".join(f"{m.role}: {m.content[:80]}" for m in omitted[-3:])
            room = max(0, max_chars-sum(len(m.content) for m in kept))
            if room: kept.insert(0, Message("system", summary[:room]))
        query = " ".join(m.content.casefold() for m in kept[-2:])
        ranked = sorted(tools, key=lambda tool: (
            -sum(word in query for word in str(tool.get("name", "")).casefold().replace("_", " ").split()),
            str(tool.get("name", "")),
        ))
        return kept, ranked[:max_tools]
