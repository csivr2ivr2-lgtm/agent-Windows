from __future__ import annotations

import importlib.util
from dataclasses import dataclass


@dataclass(frozen=True)
class IntegrationStatus:
    component: str
    integration: str
    runtime_cost: str
    default_enabled: bool
    available: bool


def _available(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def integration_matrix() -> tuple[IntegrationStatus, ...]:
    return (
        IntegrationStatus("llmfit", "on-demand hardware/model-fit wrapper", "low/on-demand", False, _available("llmfit")),
        IntegrationStatus("LiveKit Agents", "optional realtime session backend; local voice remains fallback", "optional", False, _available("livekit")),
        IntegrationStatus("Needle", "optional guarded tool execution adapter", "optional", False, _available("needle")),
        IntegrationStatus("OpenViking", "optional memory/context adapter; SQLite remains default", "optional", False, _available("openviking")),
        IntegrationStatus("Hermes", "experimental orchestration compatibility boundary", "optional", False, _available("hermes")),
        IntegrationStatus("OpenHuman", "experimental orchestration compatibility boundary", "optional", False, _available("openhuman")),
        IntegrationStatus("Unsloth", "offline training/export workflow only", "offline only", False, _available("unsloth")),
        IntegrationStatus("Soup", "offline model optimization boundary only", "offline only", False, _available("soup")),
    )


class OptionalBackend:
    """Feature-flag friendly adapter that never breaks startup when an integration is absent."""
    def __init__(self, module: str, enabled: bool = False):
        self.module = module
        self.enabled = enabled

    @property
    def healthy(self) -> bool:
        return self.enabled and _available(self.module)

    def require(self):
        if not self.healthy:
            raise RuntimeError(f"optional backend {self.module} is unavailable or disabled")
        return __import__(self.module)
