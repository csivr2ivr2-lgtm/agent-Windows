from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence


@dataclass(frozen=True)
class RouteCandidate:
    name: str
    priority: int
    score: float
    latency_ms: float | None
    cost: float | None
    quota_headroom: float | None
    last_good: bool


class OmniRoutePolicy:
    """Small Python-native router inspired by OmniRoute's auto-combo principles.

    It owns only the capabilities ai aharon needs in-process: priority, last-known-good,
    latency, optional cost/quota signals, and network-aware local preference. Provider health,
    retry, and cooldown enforcement stay in ``ProviderManager``.
    """

    VALID = {"priority", "auto", "fast", "cost", "headroom"}

    def __init__(
        self,
        strategy: str = "auto",
        *,
        costs: Mapping[str, float] | None = None,
        quota_headroom: Mapping[str, float] | None = None,
    ) -> None:
        normalized = str(strategy or "auto").strip().casefold()
        self.strategy = normalized if normalized in self.VALID else "auto"
        self.costs = {str(k): max(0.0, float(v)) for k, v in (costs or {}).items()}
        self.quota_headroom = {
            str(k): max(0.0, min(1.0, float(v))) for k, v in (quota_headroom or {}).items()
        }

    def _candidate(self, provider, state, priority: int, *, network_state: str, last_good: str | None):
        name = str(provider.name)
        latency = getattr(state, "latency_ema_ms", None)
        cost = self.costs.get(name)
        quota = self.quota_headroom.get(name)
        score = float(priority) * 10.0

        if self.strategy == "fast":
            score = latency if latency is not None else 100_000.0 + priority
        elif self.strategy == "cost":
            score = cost if cost is not None else 100_000.0 + priority
        elif self.strategy == "headroom":
            score = -(quota if quota is not None else -1.0) * 1000.0 + priority
        elif self.strategy == "auto":
            if latency is not None:
                score += min(50.0, latency / 100.0)
            if cost is not None:
                score += min(40.0, cost * 4.0)
            if quota is not None:
                score -= quota * 25.0
            if name == last_good:
                score -= 20.0
            if network_state == "POOR" and name == "local":
                score -= 35.0
            if network_state == "DEGRADED" and name == "local":
                score -= 10.0

        return RouteCandidate(name, priority, score, latency, cost, quota, name == last_good)

    def order(self, providers: Sequence[object], health: Mapping[str, object], *, network_state: str, last_good: str | None):
        if self.strategy == "priority":
            return tuple(providers)
        ranked = [
            (self._candidate(provider, health[provider.name], index, network_state=network_state, last_good=last_good), provider)
            for index, provider in enumerate(providers)
        ]
        ranked.sort(key=lambda item: (item[0].score, item[0].priority, item[0].name))
        return tuple(provider for _candidate, provider in ranked)
