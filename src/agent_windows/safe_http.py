from __future__ import annotations

import http.client
import socket
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlsplit

from .security import SecurityValidationError, validate_service_endpoint_url


class SafeHTTPError(RuntimeError):
    pass


@dataclass(frozen=True)
class SafeHTTPResponse:
    status: int
    body: bytes
    headers: dict[str, str]


def request_bytes(
    url: str,
    *,
    method: str = "GET",
    headers: Mapping[str, str] | None = None,
    body: bytes | None = None,
    timeout: float = 20.0,
    max_response_bytes: int = 8 * 1024 * 1024,
) -> SafeHTTPResponse:
    """Perform one bounded HTTP(S) request after strict endpoint validation.

    Remote plaintext HTTP is rejected by ``validate_service_endpoint_url``; loopback HTTP remains
    available for local integrations. Redirects are deliberately not followed automatically.
    """
    try:
        safe_url = validate_service_endpoint_url(url)
    except SecurityValidationError as exc:
        raise SafeHTTPError(f"unsafe HTTP endpoint: {exc}") from exc
    parsed = urlsplit(safe_url)
    host = parsed.hostname or ""
    port = parsed.port
    target = parsed.path or "/"
    if parsed.query:
        target += "?" + parsed.query
    connection_cls = (
        http.client.HTTPSConnection
        if parsed.scheme.casefold() == "https"
        else http.client.HTTPConnection
    )
    connection = connection_cls(host, port=port, timeout=max(0.1, float(timeout)))
    try:
        connection.request(method.upper(), target, body=body, headers=dict(headers or {}))
        response = connection.getresponse()
        raw = response.read(max_response_bytes + 1)
        if len(raw) > max_response_bytes:
            raise SafeHTTPError(f"HTTP response exceeds {max_response_bytes} bytes")
        return SafeHTTPResponse(
            int(response.status),
            raw,
            {str(key): str(value) for key, value in response.getheaders()},
        )
    except (socket.timeout, TimeoutError) as exc:
        raise SafeHTTPError("HTTP request timed out") from exc
    except (OSError, http.client.HTTPException) as exc:
        raise SafeHTTPError(f"HTTP connection failed: {type(exc).__name__}") from exc
    finally:
        connection.close()
