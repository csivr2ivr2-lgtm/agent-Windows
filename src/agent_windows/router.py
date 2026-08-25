from __future__ import annotations

import logging
from typing import Mapping, Sequence, Any

from .contracts import LLMProvider, LLMResponse, Message
from .errors import ProviderError, ProviderUnavailable

logger = logging.getLogger(__name__)


class LLMRouter:
    """Ordered provider fallback without bypassing quotas or retry storms."""

    def __init__(self, providers: Sequence[LLMProvider]) -> None:
        self._providers = tuple(providers)

    def complete(self, messages: Sequence[Message], tools: Sequence[Mapping[str, Any]]) -> LLMResponse:
        failures: list[str] = []
        for provider in self._providers:
            if not provider.is_available():
                failures.append(f"{provider.name}: unavailable")
                continue
            try:
                return provider.complete(messages, tools)
            except ProviderError as exc:
                logger.warning("LLM provider %s failed: %s", provider.name, exc)
                failures.append(f"{provider.name}: {exc}")
        raise ProviderUnavailable("All LLM providers failed: " + "; ".join(failures))

