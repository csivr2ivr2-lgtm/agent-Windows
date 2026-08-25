from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from .contracts import LLMProvider, LLMResponse, Message
from .errors import (
    ProviderAuthenticationError,
    ProviderConnectionError,
    ProviderError,
    ProviderRateLimited,
    ProviderServerError,
    ProviderTimeout,
    ProviderUnavailable,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 2
    base_delay: float = 0.25
    max_delay: float = 2.0
    transient_cooldown: float = 15.0
    rate_limit_cooldown: float = 60.0
    auth_cooldown: float = 300.0


@dataclass
class ProviderHealth:
    healthy: bool = True
    consecutive_failures: int = 0
    cooldown_until: float = 0.0
    last_error: str | None = None


class ProviderManager:
    """Tracks health, bounded retries, cooldowns, and ordered fallback."""

    def __init__(
        self,
        providers: Sequence[LLMProvider],
        *,
        retry_policy: RetryPolicy | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        network_monitor=None,
    ) -> None:
        self.providers = tuple(providers)
        self.retry_policy = retry_policy or RetryPolicy()
        self._clock = clock
        self._sleep = sleep
        self.network_monitor = network_monitor
        self.health = {provider.name: ProviderHealth() for provider in providers}

    def apply_network_policy(self, policy: Mapping[str, Any]) -> None:
        self.retry_policy = RetryPolicy(
            max_attempts=int(policy.get("attempts", self.retry_policy.max_attempts)),
            base_delay=self.retry_policy.base_delay, max_delay=self.retry_policy.max_delay,
            transient_cooldown=self.retry_policy.transient_cooldown,
            rate_limit_cooldown=self.retry_policy.rate_limit_cooldown,
            auth_cooldown=self.retry_policy.auth_cooldown,
        )
        providers = self.providers
        if self.network_monitor and self.network_monitor.state.value == "POOR":
            providers = tuple(sorted(providers, key=lambda provider: provider.name != "local"))
        for provider in providers:
            if hasattr(provider, "timeout"):
                provider.timeout = float(policy.get("timeout", provider.timeout))

    def complete(self, messages: Sequence[Message], tools: Sequence[Mapping[str, Any]]) -> LLMResponse:
        failures = []
        for provider in self.providers:
            if self.network_monitor and self.network_monitor.state.value == "OFFLINE" and provider.name != "local":
                failures.append(f"{provider.name}: offline")
                continue
            state = self.health[provider.name]
            now = self._clock()
            if not provider.is_available():
                failures.append(f"{provider.name}: not configured")
                continue
            if state.cooldown_until > now:
                failures.append(f"{provider.name}: cooldown")
                continue
            try:
                started = self._clock()
                response = self._attempt(provider, messages, tools)
                if self.network_monitor:
                    self.network_monitor.record(latency_ms=(self._clock()-started)*1000, success=True)
                state.healthy = True
                state.consecutive_failures = 0
                state.cooldown_until = 0.0
                state.last_error = None
                return response
            except ProviderError as exc:
                if self.network_monitor: self.network_monitor.record(success=False)
                self._record_failure(state, exc)
                logger.warning("LLM provider %s failed: %s", provider.name, exc)
                failures.append(f"{provider.name}: {exc}")
        raise ProviderUnavailable("All LLM providers failed: " + "; ".join(failures))

    def _attempt(self, provider, messages, tools):
        policy = self.retry_policy
        for attempt in range(1, max(1, policy.max_attempts) + 1):
            try:
                return provider.complete(messages, tools)
            except (ProviderAuthenticationError, ProviderRateLimited):
                raise
            except (ProviderTimeout, ProviderConnectionError, ProviderServerError):
                if attempt >= max(1, policy.max_attempts):
                    raise
                self._sleep(min(policy.max_delay, policy.base_delay * (2 ** (attempt - 1))))

    def _record_failure(self, state: ProviderHealth, exc: ProviderError) -> None:
        policy = self.retry_policy
        state.healthy = False
        state.consecutive_failures += 1
        state.last_error = str(exc)
        if isinstance(exc, ProviderRateLimited):
            cooldown = exc.retry_after if exc.retry_after is not None else policy.rate_limit_cooldown
        elif isinstance(exc, ProviderAuthenticationError):
            cooldown = policy.auth_cooldown
        else:
            cooldown = policy.transient_cooldown
        state.cooldown_until = self._clock() + max(0.0, cooldown)
