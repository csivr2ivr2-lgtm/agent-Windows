from __future__ import annotations

from typing import Any, Mapping, Sequence


class InMemoryStore:
    """Deterministic MVP backend; replace through MemoryStore, not call sites."""

    def __init__(self) -> None:
        self._items: list[str] = []

    def remember(self, text: str, *, metadata: Mapping[str, Any] | None = None) -> None:
        clean = text.strip()
        if clean and clean not in self._items:
            self._items.append(clean)

    def search(self, query: str, *, limit: int = 5) -> Sequence[str]:
        terms = {word.casefold() for word in query.split() if len(word) > 2}
        scored = []
        for index, item in enumerate(self._items):
            score = sum(term in item.casefold() for term in terms)
            if score:
                scored.append((score, index, item))
        scored.sort(key=lambda row: (-row[0], -row[1]))
        return [item for _, _, item in scored[:limit]]

