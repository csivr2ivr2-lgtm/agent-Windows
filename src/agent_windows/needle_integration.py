from __future__ import annotations

import importlib
import importlib.util
import json
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from .contracts import ToolCall


@dataclass(frozen=True)
class NeedlePlan:
    calls: tuple[ToolCall, ...] = ()
    confidence: float | None = None
    accepted: bool = False
    response_type: str = "none"
    detail: str = ""


class NeedleToolPlanner:
    """Use Needle 2 as a tiny local tool-selection model without letting it execute tools.

    The adapter only consumes tool schemas and returns candidate ``ToolCall`` objects. Actual
    execution remains inside ``AgentLoop`` and therefore still passes through ``PolicyEngine``.
    """

    name = "needle"

    def __init__(
        self,
        *,
        enabled: bool = True,
        confidence_threshold: float = 0.70,
        weights: str = "",
        max_calls: int = 3,
        accept_uncalibrated: bool = False,
        module_loader: Callable[[], object] | None = None,
    ) -> None:
        self.enabled = enabled
        self.confidence_threshold = max(0.0, min(1.0, float(confidence_threshold)))
        self.weights = weights.strip()
        self.max_calls = max(1, int(max_calls))
        self.accept_uncalibrated = accept_uncalibrated
        self._uses_default_loader = module_loader is None
        self._module_loader = module_loader or (lambda: importlib.import_module("needle"))
        self._agent = None
        self._schema_fingerprint = ""

    @property
    def available(self) -> bool:
        if not self.enabled:
            return False
        if not self._uses_default_loader:
            return True
        return importlib.util.find_spec("needle") is not None

    @staticmethod
    def _fingerprint(tools: Sequence[Mapping[str, object]]) -> str:
        return json.dumps(list(tools), ensure_ascii=False, sort_keys=True, default=str)

    def _get_agent(self, tools: Sequence[Mapping[str, object]]):
        fingerprint = self._fingerprint(tools)
        if self._agent is not None and fingerprint == self._schema_fingerprint:
            return self._agent
        module = self._module_loader()
        needle_cls = getattr(module, "Needle")
        kwargs = {
            "tools": [dict(schema) for schema in tools],
            "system": (
                "Select only the minimum tools required for the user's request. "
                "Do not invent tool names and do not execute anything yourself."
            ),
        }
        if self.weights:
            kwargs["weights"] = self.weights
        self._agent = needle_cls(**kwargs)
        self._schema_fingerprint = fingerprint
        return self._agent

    def plan(self, query: str, tools: Sequence[Mapping[str, object]]) -> NeedlePlan:
        if not self.enabled:
            return NeedlePlan(detail="disabled")
        if not self.available:
            return NeedlePlan(detail="package unavailable")
        if not tools:
            return NeedlePlan(detail="no tools")
        try:
            agent = self._get_agent(tools)
            response = agent.complete(query)
        except (ImportError, OSError, RuntimeError, AttributeError, TypeError, ValueError) as exc:
            return NeedlePlan(detail=f"unavailable: {type(exc).__name__}: {exc}")

        if not isinstance(response, Mapping):
            return NeedlePlan(detail="malformed response")
        response_type = str(response.get("type") or "none")
        raw_confidence = response.get("confidence")
        confidence = float(raw_confidence) if isinstance(raw_confidence, (int, float)) else None
        raw_calls = response.get("function_calls") or []
        allowed_names = {str(tool.get("name", "")) for tool in tools}
        calls: list[ToolCall] = []
        if isinstance(raw_calls, Sequence) and not isinstance(raw_calls, (str, bytes, bytearray)):
            for raw in raw_calls:
                if len(calls) >= self.max_calls or not isinstance(raw, Mapping):
                    break
                name = str(raw.get("name") or "")
                arguments = raw.get("arguments") or {}
                if name not in allowed_names or not isinstance(arguments, Mapping):
                    continue
                calls.append(ToolCall(name, dict(arguments)))

        calibrated = confidence is not None
        accepted = bool(
            response_type == "call"
            and calls
            and (
                confidence >= self.confidence_threshold
                if calibrated
                else self.accept_uncalibrated
            )
        )
        detail = "accepted" if accepted else "below confidence threshold or no valid call"
        return NeedlePlan(tuple(calls) if accepted else (), confidence, accepted, response_type, detail)
