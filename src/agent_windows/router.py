from __future__ import annotations

from typing import Mapping, Sequence, Any

from .contracts import LLMProvider, LLMResponse, Message
from .provider_manager import ProviderManager


class LLMRouter:
    """Ordered provider fallback without bypassing quotas or retry storms."""

    def __init__(self, providers: Sequence[LLMProvider] | ProviderManager) -> None:
        self.manager = providers if isinstance(providers, ProviderManager) else ProviderManager(providers)

    def complete(self, messages: Sequence[Message], tools: Sequence[Mapping[str, Any]]) -> LLMResponse:
        return self.manager.complete(messages, tools)
