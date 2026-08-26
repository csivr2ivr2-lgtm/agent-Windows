from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass
from typing import Sequence

from .contracts import Message
from .errors import ProviderError


_HTTP_STATUS = re.compile(r"HTTP\s+(\d{3})", re.IGNORECASE)


@dataclass(frozen=True)
class ProviderCheck:
    provider: str
    status: str
    latency_ms: float | None = None
    http_status: int | None = None
    error_type: str | None = None
    detail: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _status_from_error(exc: BaseException) -> int | None:
    match = _HTTP_STATUS.search(str(exc))
    return int(match.group(1)) if match else None


def check_provider(provider, *, clock=time.monotonic) -> ProviderCheck:
    if not provider.is_available():
        return ProviderCheck(provider.name, "UNCONFIGURED")
    started = clock()
    try:
        response = provider.complete([Message("user", "Reply with OK only.")], [])
    except ProviderError as exc:
        return ProviderCheck(
            provider.name,
            "FAIL",
            round((clock() - started) * 1000, 1),
            _status_from_error(exc),
            type(exc).__name__,
            str(exc)[:300],
        )
    except Exception as exc:
        return ProviderCheck(
            provider.name,
            "FAIL",
            round((clock() - started) * 1000, 1),
            None,
            type(exc).__name__,
            str(exc)[:300],
        )
    return ProviderCheck(
        provider.name,
        "OK",
        round((clock() - started) * 1000, 1),
        None,
        None,
        response.text[:80] if getattr(response, "text", "") else None,
    )


def check_providers(providers: Sequence[object]) -> list[ProviderCheck]:
    return [check_provider(provider) for provider in providers]
