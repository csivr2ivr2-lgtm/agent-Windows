from __future__ import annotations

import json
import socket
from typing import Iterator, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

from .audio import AudioChunk, AudioTransport, ChunkAck
from .errors import ProviderBadResponse, ProviderError
from .http import HTTPResponse, _read_limited
from .speech import BinaryHTTPClient, UrllibBinaryClient, _check


class BinaryHTTPStreamClient(Protocol):
    def iter_bytes(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float, *, chunk_size: int = 8192
    ) -> Iterator[bytes]: ...


class UrllibStreamingClient:
    def iter_bytes(self, method, url, headers, body, timeout, *, chunk_size=8192):
        try:
            with urlopen(Request(url, data=body, headers=dict(headers), method=method), timeout=timeout) as response:
                while True:
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk
        except HTTPError as exc:
            response = HTTPResponse(exc.code, _read_limited(exc, 1024 * 1024), dict(exc.headers.items()))
            _check(response, "relay tts")
        except (TimeoutError, socket.timeout) as exc:
            from .errors import ProviderTimeout
            raise ProviderTimeout(str(exc)) from exc
        except URLError as exc:
            from .errors import ProviderConnectionError, ProviderTimeout
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise ProviderTimeout(str(exc.reason)) from exc
            raise ProviderConnectionError(str(exc.reason)) from exc
        except OSError as exc:
            from .errors import ProviderConnectionError
            raise ProviderConnectionError(str(exc)) from exc


class RelayAudioTransport(AudioTransport):
    def __init__(self, base_url: str, token: str, *, client: BinaryHTTPClient | None = None, stream_client: BinaryHTTPStreamClient | None = None, timeout=30):
        self.base_url, self.token, self.client, self.timeout = base_url.rstrip("/"), token, client or UrllibBinaryClient(), timeout
        self.stream_client = stream_client or UrllibStreamingClient()
        self.received_sequences: dict[str, set[int]] = {}

    def is_available(self):
        parsed = urlsplit(self.base_url)
        return bool(
            parsed.scheme == "https" and parsed.hostname and not parsed.username
            and not parsed.password and not parsed.query and not parsed.fragment and self.token
        )
    def _headers(self, content_type="application/json"):
        return {"Authorization": f"Bearer {self.token}", "Content-Type": content_type}

    @staticmethod
    def _json(response, operation: str) -> dict:
        try:
            data = response.json()
        except (UnicodeDecodeError, ValueError) as exc:
            raise ProviderBadResponse(f"relay {operation} response is not valid JSON") from exc
        if not isinstance(data, dict):
            raise ProviderBadResponse(f"relay {operation} response must be an object")
        return data

    def health(self) -> bool:
        if not self.is_available(): return False
        try:
            response = self.client.request("GET", self.base_url + "/v1/health", self._headers(), None, min(10, self.timeout))
            return response.status == 200
        except (ProviderError, OSError, ValueError): return False

    def open(self, session_id: str, metadata: dict) -> None:
        body = json.dumps({"session_id": session_id, "resume": True, **metadata}).encode()
        response = self.client.request("POST", self.base_url + "/v1/audio/sessions", self._headers(), body, self.timeout)
        _check(response, "relay")
        data = self._json(response, "session")
        try:
            received = {int(value) for value in data.get("received_sequences", [])}
        except (TypeError, ValueError) as exc:
            raise ProviderBadResponse("relay session response has invalid sequences") from exc
        self.received_sequences[session_id] = received

    def send_chunk(self, chunk: AudioChunk) -> ChunkAck:
        url = f"{self.base_url}/v1/audio/sessions/{quote(chunk.session_id, safe='')}/chunks/{chunk.sequence}"
        headers = self._headers("application/octet-stream") | {
            "X-Audio-Timestamp-Ms": str(chunk.timestamp_ms), "X-Chunk-SHA256": chunk.checksum,
            "X-Final-Chunk": "1" if chunk.final else "0",
        }
        response = self.client.request("PUT", url, headers, chunk.payload, self.timeout)
        _check(response, "relay")
        data = self._json(response, "chunk")
        try:
            return ChunkAck(data["session_id"], int(data["sequence"]), bool(data.get("accepted", True)), bool(data.get("duplicate", False)))
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderBadResponse("relay chunk response is malformed") from exc

    def finish(self, session_id: str) -> dict:
        url = f"{self.base_url}/v1/audio/sessions/{quote(session_id, safe='')}/finish"
        response = self.client.request("POST", url, self._headers(), b"{}", self.timeout)
        _check(response, "relay")
        return self._json(response, "finish")

    def iter_tts(self, text: str, *, language: str = "he", chunk_size: int = 8192):
        if not self.is_available():
            raise ProviderBadResponse("relay tts is not configured")
        payload = json.dumps({"text": text, "language": language}, ensure_ascii=False).encode("utf-8")
        headers = self._headers() | {"Accept": "audio/mpeg"}
        yield from self.stream_client.iter_bytes(
            "POST", self.base_url + "/v1/tts/stream", headers, payload, self.timeout, chunk_size=chunk_size
        )
