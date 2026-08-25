from __future__ import annotations

from dataclasses import dataclass

from .audio.adaptation import NetworkState


@dataclass
class NetworkMonitor:
    state: NetworkState = NetworkState.GOOD
    latency_ms: float | None = None
    failure_score: float = 0.0

    def record(self, *, latency_ms: float | None = None, success: bool = True) -> NetworkState:
        if latency_ms is not None:
            self.latency_ms = latency_ms if self.latency_ms is None else self.latency_ms * 0.7 + latency_ms * 0.3
        self.failure_score = max(0.0, self.failure_score * 0.7 + (0.35 if not success else -0.15))
        if self.failure_score >= 0.8:
            self.state = NetworkState.OFFLINE
        elif self.failure_score >= 0.45 or (self.latency_ms or 0) > 2500:
            self.state = NetworkState.POOR
        elif self.failure_score >= 0.2 or (self.latency_ms or 0) > 900:
            self.state = NetworkState.DEGRADED
        else:
            self.state = NetworkState.GOOD
        return self.state

    def policy(self) -> dict[str, int | float]:
        return {
            NetworkState.GOOD: {"timeout": 30, "attempts": 2, "context_chars": 12000, "tools": 20},
            NetworkState.DEGRADED: {"timeout": 45, "attempts": 2, "context_chars": 7000, "tools": 10},
            NetworkState.POOR: {"timeout": 65, "attempts": 3, "context_chars": 3500, "tools": 5},
            NetworkState.OFFLINE: {"timeout": 5, "attempts": 1, "context_chars": 1500, "tools": 5},
        }[self.state]

