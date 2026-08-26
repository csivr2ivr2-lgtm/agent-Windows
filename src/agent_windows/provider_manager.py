from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from .contracts import LLMProvider, LLMResponse, LLMStreamEvent, Message
from .omniroute_policy import OmniRoutePolicy
from .errors import (
    ProviderAuthenticationError,
    ProviderConnectionError,
    ProviderError,
    ProviderPermissionError,
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
    latency_ema_ms: float | None = None
    successes: int = 0
    failures: int = 0
    last_success_at: float | None = None


class ProviderManager:
    """Tracks health, bounded retries, cooldowns, ordered fallback and streaming events."""

    def __init__(
        self,
        providers: Sequence[LLMProvider],
        *,
        retry_policy: RetryPolicy | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        network_monitor=None,
        routing_strategy: str = "priority",
        provider_costs: Mapping[str, float] | None = None,
        provider_quota_headroom: Mapping[str, float] | None = None,
    ) -> None:
        self.providers = tuple(providers)
        names = [provider.name for provider in providers]
        if len(names) != len(set(names)):
            raise ValueError("provider names must be unique")
        self.retry_policy = retry_policy or RetryPolicy()
        self._clock = clock
        self._sleep = sleep
        self.network_monitor = network_monitor
        self.health = {provider.name: ProviderHealth() for provider in providers}
        self.route_policy = OmniRoutePolicy(
            routing_strategy, costs=provider_costs, quota_headroom=provider_quota_headroom
        )
        self.last_good_provider: str | None = None

    def apply_network_policy(self, policy: Mapping[str, Any]) -> None:
        self.retry_policy = RetryPolicy(
            max_attempts=int(policy.get("attempts", self.retry_policy.max_attempts)),
            base_delay=self.retry_policy.base_delay,
            max_delay=self.retry_policy.max_delay,
            transient_cooldown=self.retry_policy.transient_cooldown,
            rate_limit_cooldown=self.retry_policy.rate_limit_cooldown,
            auth_cooldown=self.retry_policy.auth_cooldown,
        )
        for provider in self.providers:
            if hasattr(provider, "timeout"):
                provider.timeout = float(policy.get("timeout", provider.timeout))

    def _ordered_providers(self):
        network_state = self.network_monitor.state.value if self.network_monitor else "GOOD"
        providers = self.route_policy.order(
            self.providers,
            self.health,
            network_state=network_state,
            last_good=self.last_good_provider,
        )
        if self.route_policy.strategy == "priority" and network_state == "POOR":
            providers = tuple(sorted(providers, key=lambda provider: provider.name != "local"))
        return providers

    def _skip_reason(self, provider, state: ProviderHealth, now: float) -> str | None:
        if self.network_monitor and self.network_monitor.state.value == "OFFLINE" and provider.name != "local":
            return "offline"
        if not provider.is_available():
            return "not configured"
        if state.cooldown_until > now:
            return "cooldown"
        return None

    def complete(self, messages: Sequence[Message], tools: Sequence[Mapping[str, Any]]) -> LLMResponse:
        failures = []
        for provider in self._ordered_providers():
            state = self.health[provider.name]
            reason = self._skip_reason(provider, state, self._clock())
            if reason:
                failures.append(f"{provider.name}: {reason}")
                continue
            try:
                started = self._clock()
                response = self._attempt(provider, messages, tools)
                latency_ms = (self._clock() - started) * 1000
                if self.network_monitor:
                    self.network_monitor.record(latency_ms=latency_ms, success=True)
                self._mark_success(provider.name, state, latency_ms)
                return response
            except ProviderError as exc:
                if self.network_monitor:
                    self.network_monitor.record(success=False)
                self._record_failure(state, exc)
                logger.warning("LLM provider %s failed: %s", provider.name, exc)
                failures.append(f"{provider.name}: {exc}")
        raise ProviderUnavailable("All LLM providers failed: " + "; ".join(failures))

    def stream_events(
        self,
        messages: Sequence[Message],
        tools: Sequence[Mapping[str, Any]],
        *,
        cancel_event=None,
    ):
        """Yield provider-neutral text/tool events with ordered fallback.

        Fallback is only allowed before the first externally visible event. Once output has
        escaped a provider, switching providers could duplicate speech or execute a tool twice,
        so a mid-stream failure terminates that turn instead of replaying it elsewhere.
        """
        failures = []
        for provider in self._ordered_providers():
            state = self.health[provider.name]
            reason = self._skip_reason(provider, state, self._clock())
            if reason:
                failures.append(f"{provider.name}: {reason}")
                continue
            if cancel_event is not None and cancel_event.is_set():
                return
            emitted = False
            try:
                started = self._clock()
                if hasattr(provider, "stream_events"):
                    source = provider.stream_events(messages, tools, cancel_event=cancel_event)
                elif hasattr(provider, "stream"):
                    source = (
                        LLMStreamEvent.text_delta(provider.name, chunk)
                        for chunk in provider.stream(messages, tools, cancel_event=cancel_event)
                        if chunk
                    )
                else:
                    response = self._attempt(provider, messages, tools)
                    events = []
                    if response.text:
                        events.append(LLMStreamEvent.text_delta(provider.name, response.text))
                    events.extend(LLMStreamEvent.call(provider.name, call) for call in response.tool_calls)
                    source = iter(events)

                for event in source:
                    if cancel_event is not None and cancel_event.is_set():
                        return
                    emitted = True
                    yield event

                latency_ms = (self._clock() - started) * 1000
                if self.network_monitor:
                    self.network_monitor.record(latency_ms=latency_ms, success=True)
                self._mark_success(provider.name, state, latency_ms)
                return
            except ProviderError as exc:
                if self.network_monitor:
                    self.network_monitor.record(success=False)
                self._record_failure(state, exc)
                logger.warning("LLM provider %s stream failed: %s", provider.name, exc)
                if emitted:
                    raise ProviderUnavailable(
                        f"{provider.name} stream failed after output began: {exc}"
                    ) from exc
                failures.append(f"{provider.name}: {exc}")
        if failures:
            raise ProviderUnavailable("All LLM providers failed: " + "; ".join(failures))

    def stream(self, messages: Sequence[Message], tools: Sequence[Mapping[str, Any]], *, cancel_event=None):
        """Backward-compatible text-only view over ``stream_events``."""
        for event in self.stream_events(messages, tools, cancel_event=cancel_event):
            if event.kind == "text" and event.text:
                yield event.text

    def _mark_success(self, provider_name: str, state: ProviderHealth, latency_ms: float) -> None:
        state.healthy = True
        state.consecutive_failures = 0
        state.cooldown_until = 0.0
        state.last_error = None
        state.successes += 1
        state.last_success_at = self._clock()
        measured = max(0.0, float(latency_ms))
        state.latency_ema_ms = (
            measured
            if state.latency_ema_ms is None
            else (state.latency_ema_ms * 0.7) + (measured * 0.3)
        )
        self.last_good_provider = provider_name

    def routing_snapshot(self) -> list[dict[str, object]]:
        network_state = self.network_monitor.state.value if self.network_monitor else "GOOD"
        ordered = self._ordered_providers()
        rank = {provider.name: index for index, provider in enumerate(ordered)}
        return [
            {
                "provider": provider.name,
                "rank": rank[provider.name],
                "healthy": self.health[provider.name].healthy,
                "latency_ema_ms": self.health[provider.name].latency_ema_ms,
                "successes": self.health[provider.name].successes,
                "failures": self.health[provider.name].failures,
                "cooldown_until": self.health[provider.name].cooldown_until,
                "last_good": provider.name == self.last_good_provider,
                "network_state": network_state,
                "strategy": self.route_policy.strategy,
            }
            for provider in ordered
        ]

    def _attempt(self, provider, messages, tools) -> LLMResponse:
        policy = self.retry_policy
        last_error: ProviderError | None = None
        for attempt in range(1, max(1, policy.max_attempts) + 1):
            try:
                return provider.complete(messages, tools)
            except (ProviderAuthenticationError, ProviderPermissionError, ProviderRateLimited):
                raise
            except (ProviderTimeout, ProviderConnectionError, ProviderServerError) as exc:
                last_error = exc
                if attempt >= max(1, policy.max_attempts):
                    raise
                delay = min(policy.base_delay * (2 ** (attempt - 1)), policy.max_delay)
                self._sleep(max(0.0, delay))
        assert last_error is not None
        raise last_error

    def _record_failure(self, state: ProviderHealth, exc: ProviderError) -> None:
        policy = self.retry_policy
        state.healthy = False
        state.consecutive_failures += 1
        state.failures += 1
        state.last_error = str(exc)
        if isinstance(exc, ProviderRateLimited):
            cooldown = exc.retry_after if exc.retry_after is not None else policy.rate_limit_cooldown
        elif isinstance(exc, (ProviderAuthenticationError, ProviderPermissionError)):
            cooldown = policy.auth_cooldown
        else:
            cooldown = policy.transient_cooldown
        state.cooldown_until = self._clock() + max(0.0, cooldown)
