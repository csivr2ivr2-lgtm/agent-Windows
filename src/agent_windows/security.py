from __future__ import annotations

import ipaddress
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


class SecurityValidationError(ValueError):
    """Raised when a path or URL crosses an ai aharon trust boundary."""


def _parsed_http_url(value: str):
    raw = str(value or "").strip()
    if not raw or len(raw) > 4096:
        raise SecurityValidationError("URL is missing or too long")
    parsed = urlsplit(raw)
    if parsed.scheme.casefold() not in {"http", "https"}:
        raise SecurityValidationError("only http/https URLs are allowed")
    if not parsed.hostname:
        raise SecurityValidationError("URL hostname is required")
    if parsed.username is not None or parsed.password is not None:
        raise SecurityValidationError("credentials in URLs are not allowed")
    if parsed.fragment:
        raise SecurityValidationError("URL fragments are not allowed")
    try:
        port = parsed.port
    except ValueError as exc:
        raise SecurityValidationError("invalid URL port") from exc
    if port is not None and not 1 <= port <= 65535:
        raise SecurityValidationError("invalid URL port")
    return parsed


def _host_is_loopback(hostname: str) -> bool:
    host = hostname.strip("[]").casefold()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_service_endpoint_url(value: str, *, allow_loopback_http: bool = True) -> str:
    """Validate a concrete service request URL while preserving its path and query."""
    parsed = _parsed_http_url(value)
    if parsed.scheme.casefold() == "http" and not (
        allow_loopback_http and _host_is_loopback(parsed.hostname or "")
    ):
        raise SecurityValidationError("remote service URLs must use https")
    return urlunsplit((parsed.scheme.casefold(), parsed.netloc, parsed.path or "/", parsed.query, ""))


def validate_service_base_url(value: str, *, allow_loopback_http: bool = True) -> str:
    """Validate operator-configured service endpoints.

    Remote endpoints must use TLS. Plain HTTP is allowed only for loopback services such as
    Ollama/Wigolo/OpenViking running on the same machine.
    """
    parsed = _parsed_http_url(value)
    if parsed.path not in {"", "/"} or parsed.query:
        raise SecurityValidationError("service base URL must not contain a path or query")
    if parsed.scheme.casefold() == "http" and not (
        allow_loopback_http and _host_is_loopback(parsed.hostname or "")
    ):
        raise SecurityValidationError("remote service URLs must use https")
    host = parsed.hostname or ""
    netloc = f"[{host}]" if ":" in host and not host.startswith("[") else host
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    return urlunsplit((parsed.scheme.casefold(), netloc, "", "", "")).rstrip("/")


def validate_external_http_url(value: str) -> str:
    """Reject obvious SSRF targets before handing a URL to a web backend.

    The web backend performs the actual fetch. This local guard rejects credentials, local host
    names, and literal private/link-local/reserved IP addresses. DNS is deliberately not resolved
    here so validation cannot itself become a network operation; backends must also enforce their
    own egress policy.
    """
    parsed = _parsed_http_url(value)
    host = (parsed.hostname or "").strip("[]").casefold()
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise SecurityValidationError("local/internal fetch targets are not allowed")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        raise SecurityValidationError("private or non-routable fetch targets are not allowed")
    return urlunsplit((parsed.scheme.casefold(), parsed.netloc, parsed.path or "/", parsed.query, ""))


def resolve_within(root: str | Path, candidate: str | Path, *, must_exist: bool = False) -> Path:
    """Resolve a path and require it to remain below *root*."""
    root_path = Path(root).expanduser().resolve()
    target = Path(candidate).expanduser().resolve()
    if target != root_path and root_path not in target.parents:
        raise SecurityValidationError("path escapes allowed root")
    if must_exist and not target.exists():
        raise FileNotFoundError(target)
    return target
