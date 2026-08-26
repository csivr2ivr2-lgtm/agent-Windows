from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from typing import Iterator, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .errors import ProviderBadResponse, ProviderConnectionError, ProviderTimeout

MAX_JSON_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_STREAM_LINE_BYTES = 1024 * 1024


def _read_limited(stream, limit: int = MAX_JSON_RESPONSE_BYTES) -> bytes:
    body = stream.read(limit + 1)
    if len(body) > limit:
        raise ProviderBadResponse(f"HTTP response exceeded {limit} bytes")
    return body


@dataclass(frozen=True)
class HTTPResponse:
    status: int
    body: bytes
    headers: Mapping[str, str]

    def json(self):
        return json.loads(self.body.decode("utf-8"))


class HTTPStatusError(RuntimeError):
    def __init__(self, response: HTTPResponse):
        self.response = response
        super().__init__(f"HTTP {response.status}")


class HTTPTransport(Protocol):
    def post_json(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: object,
        timeout: float,
    ) -> HTTPResponse: ...


class UrllibTransport:
    """Small synchronous transport from the Python standard library."""

    def _request(self, url: str, headers: Mapping[str, str], payload: object) -> Request:
        return Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", **headers},
            method="POST",
        )

    def post_json(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: object,
        timeout: float,
    ) -> HTTPResponse:
        request = self._request(url, headers, payload)
        try:
            with urlopen(request, timeout=timeout) as response:
                return HTTPResponse(
                    response.status,
                    _read_limited(response),
                    dict(response.headers.items()),
                )
        except HTTPError as exc:
            return HTTPResponse(exc.code, _read_limited(exc), dict(exc.headers.items()))
        except (TimeoutError, socket.timeout) as exc:
            raise ProviderTimeout(str(exc) or "request timed out") from exc
        except URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise ProviderTimeout(str(exc.reason) or "request timed out") from exc
            raise ProviderConnectionError(str(exc.reason)) from exc
        except OSError as exc:
            raise ProviderConnectionError(str(exc)) from exc

    def stream_sse(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: object,
        timeout: float,
    ) -> Iterator[bytes]:
        """Yield SSE ``data:`` payloads without buffering the whole response."""
        request = self._request(url, {"Accept": "text/event-stream", **headers}, payload)
        try:
            with urlopen(request, timeout=timeout) as response:
                for raw_line in response:
                    if len(raw_line) > MAX_STREAM_LINE_BYTES:
                        raise ProviderBadResponse(
                            f"HTTP stream line exceeded {MAX_STREAM_LINE_BYTES} bytes"
                        )
                    line = raw_line.strip()
                    if not line or line.startswith(b":"):
                        continue
                    if line.startswith(b"data:"):
                        yield line[5:].lstrip()
        except HTTPError as exc:
            response = HTTPResponse(exc.code, _read_limited(exc), dict(exc.headers.items()))
            raise HTTPStatusError(response) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise ProviderTimeout(str(exc) or "stream timed out") from exc
        except URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise ProviderTimeout(str(exc.reason) or "stream timed out") from exc
            raise ProviderConnectionError(str(exc.reason)) from exc
        except OSError as exc:
            raise ProviderConnectionError(str(exc)) from exc
