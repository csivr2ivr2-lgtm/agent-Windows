from __future__ import annotations

import json
from urllib.parse import quote, urlsplit

from .audio import AudioChunk, AudioTransport, ChunkAck
from .errors import ProviderError
from .speech import BinaryHTTPClient, UrllibBinaryClient, _check


class RelayAudioTransport(AudioTransport):
    def __init__(self, base_url: str, token: str, *, client: BinaryHTTPClient | None = None, timeout=30):
        self.base_url, self.token, self.client, self.timeout = base_url.rstrip("/"), token, client or UrllibBinaryClient(), timeout
        self.received_sequences: dict[str, set[int]] = {}

    def is_available(self):
        parsed = urlsplit(self.base_url)
        return bool(
            parsed.scheme == "https" and parsed.hostname and not parsed.username
            and not parsed.password and not parsed.query and not parsed.fragment and self.token
        )
    def _headers(self, content_type="application/json"):
        return {"Authorization": f"Bearer {self.token}", "Content-Type": content_type}

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
        data = response.json()
        self.received_sequences[session_id] = {int(value) for value in data.get("received_sequences", [])}

    def send_chunk(self, chunk: AudioChunk) -> ChunkAck:
        url = f"{self.base_url}/v1/audio/sessions/{quote(chunk.session_id, safe='')}/chunks/{chunk.sequence}"
        headers = self._headers("application/octet-stream") | {
            "X-Audio-Timestamp-Ms": str(chunk.timestamp_ms), "X-Chunk-SHA256": chunk.checksum,
            "X-Final-Chunk": "1" if chunk.final else "0",
        }
        response = self.client.request("PUT", url, headers, chunk.payload, self.timeout)
        _check(response, "relay")
        data = response.json()
        return ChunkAck(data["session_id"], int(data["sequence"]), bool(data.get("accepted", True)), bool(data.get("duplicate", False)))

    def finish(self, session_id: str) -> dict:
        url = f"{self.base_url}/v1/audio/sessions/{quote(session_id, safe='')}/finish"
        response = self.client.request("POST", url, self._headers(), b"{}", self.timeout)
        _check(response, "relay")
        return response.json()
