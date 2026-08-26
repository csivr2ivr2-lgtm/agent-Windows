from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Callable, Mapping


class RiskLevel(IntEnum):
    READ_ONLY = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4

    @classmethod
    def parse(cls, value: object) -> "RiskLevel":
        if isinstance(value, cls):
            return value
        normalized = str(value or "").strip().replace("-", "_").upper()
        aliases = {
            "READONLY": "READ_ONLY",
            "READ": "READ_ONLY",
        }
        normalized = aliases.get(normalized, normalized)
        try:
            return cls[normalized]
        except KeyError as exc:
            raise ValueError(f"unknown risk level: {value!r}") from exc


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    requires_confirmation: bool
    risk: RiskLevel
    reason: str = ""
    action_hash: str = ""


@dataclass(frozen=True)
class ConfirmationGrant:
    action_hash: str
    expires_at: float

    def valid_for(self, action_hash: str, *, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else now
        return self.action_hash == action_hash and current <= self.expires_at


def action_hash(name: str, arguments: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        {"name": name, "arguments": arguments},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class PolicyEngine:
    """Deterministic guardrail between model-selected tools and side effects.

    Unknown legacy tools are treated as LOW risk by default to preserve compatibility
    while the repository migrates every tool to explicit risk metadata. Strict callers
    can set ``allow_unclassified=False`` to require an explicit risk declaration.
    """

    def __init__(
        self,
        *,
        confirmation_at: RiskLevel = RiskLevel.MEDIUM,
        allow_unclassified: bool = True,
        deny: Callable[[str, Mapping[str, Any]], bool] | None = None,
    ) -> None:
        self.confirmation_at = confirmation_at
        self.allow_unclassified = allow_unclassified
        self.deny = deny

    def evaluate(
        self,
        tool: object,
        arguments: Mapping[str, Any],
        *,
        grant: ConfirmationGrant | None = None,
    ) -> PolicyDecision:
        name = str(getattr(tool, "name", "unknown"))
        raw_risk = getattr(tool, "risk", None)
        if raw_risk is None:
            if not self.allow_unclassified:
                return PolicyDecision(False, False, RiskLevel.MEDIUM, "tool risk is not declared")
            risk = RiskLevel.LOW
        else:
            risk = RiskLevel.parse(raw_risk)

        digest = action_hash(name, arguments)
        if self.deny and self.deny(name, arguments):
            return PolicyDecision(False, False, risk, "blocked by policy", digest)

        if risk >= self.confirmation_at:
            confirmed = bool(grant and grant.valid_for(digest))
            return PolicyDecision(
                confirmed,
                not confirmed,
                risk,
                "explicit confirmation required" if not confirmed else "confirmed",
                digest,
            )
        return PolicyDecision(True, False, risk, action_hash=digest)

    @staticmethod
    def grant(name: str, arguments: Mapping[str, Any], *, ttl_seconds: float = 30.0) -> ConfirmationGrant:
        return ConfirmationGrant(action_hash(name, arguments), time.monotonic() + ttl_seconds)
