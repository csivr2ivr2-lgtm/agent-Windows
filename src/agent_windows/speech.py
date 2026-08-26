from __future__ import annotations

import json
import socket
import time
from dataclasses import dataclass
from typing import Iterator, Mapping, Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from .errors import ProviderAuthenticationError, ProviderBadResponse, ProviderConnectionError, ProviderPermissionError, ProviderRateLimited, ProviderServerError, ProviderTimeout
from .http import HTTPResponse, _read_limited


class BinaryHTTPClient(Protocol):
    def request(self, method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float) -> HTTPResponse: ...


class UrllibBinaryClient:
    def request(self, method, url, headers, body, timeout):
        try:
            with urlopen(Request(url, data=body, headers=dict(headers), method=method), timeout=timeout) as response:
                return HTTPResponse(response.status, _read_limited(response, 32 * 1024 * 1024), dict(response.headers.items()))
        except HTTPError as exc:
            return HTTPResponse(exc.code, _read_limited(exc), dict(exc.headers.items()))
        except (TimeoutError, socket.timeout) as exc:
            raise ProviderTimeout(str(exc)) from exc
        except URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise ProviderTimeout(str(exc.reason)) from exc
            raise ProviderConnectionError(str(exc.reason)) from exc
        except OSError as exc:
            raise ProviderConnectionError(str(exc)) from exc

    def iter_request(self, method, url, headers, body, timeout, *, chunk_size=4096) -> Iterator[bytes]:
        try:
            with urlopen(Request(url, data=body, headers=dict(headers), method=method), timeout=timeout) as response:
                while True:
                    chunk = response.read(chunk_size)
                    if not chunk:
                        return
                    yield chunk
        except HTTPError as exc:
            response = HTTPResponse(exc.code, _read_limited(exc), dict(exc.headers.items()))
            _check(response, "streaming HTTP")
        except (TimeoutError, socket.timeout) as exc:
            raise ProviderTimeout(str(exc)) from exc
        except URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise ProviderTimeout(str(exc.reason)) from exc
            raise ProviderConnectionError(str(exc.reason)) from exc
        except OSError as exc:
            raise ProviderConnectionError(str(exc)) from exc


def _check(response: HTTPResponse, provider: str):
    if response.status == 401: raise ProviderAuthenticationError(f"{provider} authentication failed (HTTP 401)")
    if response.status == 403: raise ProviderPermissionError(f"{provider} permission/model access denied (HTTP 403)")
    if response.status == 429: raise ProviderRateLimited(f"{provider} rate limited")
    if response.status >= 500: raise ProviderServerError(f"{provider} server error {response.status}")
    if not 200 <= response.status < 300: raise ProviderBadResponse(f"{provider} HTTP {response.status}")


class AssemblyAISTT:
    name = "assemblyai"
    supported_codecs = {"ogg_opus", "mp3", "pcm_s16le"}

    def __init__(self, api_key: str, *, client: BinaryHTTPClient | None = None, timeout: float = 45, poll_seconds: float = 1):
        self.api_key, self.client, self.timeout, self.poll_seconds = api_key.strip(), client or UrllibBinaryClient(), timeout, poll_seconds

    def is_available(self): return bool(self.api_key)

    def transcribe(self, audio: bytes, *, content_type="audio/ogg", language: str | None = "he") -> str:
        headers = {"authorization": self.api_key, "Content-Type": content_type}
        uploaded = self.client.request("POST", "https://api.assemblyai.com/v2/upload", headers, audio, self.timeout)
        _check(uploaded, self.name)
        try: upload_url = uploaded.json()["upload_url"]
        except (KeyError, ValueError) as exc: raise ProviderBadResponse("assemblyai upload response malformed") from exc
        payload = {"audio_url": upload_url}
        if language: payload["language_code"] = language
        submitted = self.client.request("POST", "https://api.assemblyai.com/v2/transcript", {"authorization": self.api_key,"Content-Type":"application/json"}, json.dumps(payload).encode(), self.timeout)
        _check(submitted, self.name)
        try: transcript_id = submitted.json()["id"]
        except (KeyError, ValueError) as exc: raise ProviderBadResponse("assemblyai submit response malformed") from exc
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            result = self.client.request("GET", f"https://api.assemblyai.com/v2/transcript/{transcript_id}", {"authorization": self.api_key}, None, self.timeout)
            _check(result, self.name)
            data = result.json()
            if data.get("status") == "completed": return data.get("text", "")
            if data.get("status") == "error": raise ProviderBadResponse("assemblyai transcription failed")
            time.sleep(self.poll_seconds)
        raise ProviderTimeout("assemblyai polling timed out")


class DeepgramSTT:
    name = "deepgram"
    supported_codecs = {"ogg_opus", "mp3", "pcm_s16le"}

    def __init__(self, api_key: str, *, model="nova-3", client: BinaryHTTPClient | None = None, timeout=45):
        self.api_key, self.model, self.client, self.timeout = api_key.strip(), model, client or UrllibBinaryClient(), timeout

    def is_available(self): return bool(self.api_key)

    def transcribe(self, audio: bytes, *, content_type="audio/ogg", language: str | None = "he") -> str:
        query = urlencode({"model": self.model, "language": language or "multi", "smart_format": "true"})
        result = self.client.request("POST", f"https://api.deepgram.com/v1/listen?{query}",
                                     {"Authorization": f"Token {self.api_key}", "Content-Type": content_type}, audio, self.timeout)
        _check(result, self.name)
        try: return result.json()["results"]["channels"][0]["alternatives"][0]["transcript"]
        except (KeyError, IndexError, ValueError) as exc: raise ProviderBadResponse("deepgram response malformed") from exc


class STTManager:
    def __init__(self, providers): self.providers = tuple(providers)
    def transcribe(self, audio: bytes, *, content_type="audio/ogg", language="he"):
        errors = []
        for provider in self.providers:
            if not provider.is_available(): continue
            try: return provider.transcribe(audio, content_type=content_type, language=language)
            except (ProviderConnectionError, ProviderTimeout, ProviderRateLimited, ProviderServerError, ProviderBadResponse) as exc: errors.append(str(exc))
            except ProviderAuthenticationError as exc: errors.append(f"configuration: {exc}")
        raise ProviderConnectionError("No STT provider succeeded: " + "; ".join(errors))


class ElevenLabsTTS:
    name = "elevenlabs"
    def __init__(self, api_key: str, voice_id: str, *, model="eleven_v3", client: BinaryHTTPClient | None = None, timeout=45):
        self.api_key, self.voice_id, self.model, self.client, self.timeout = api_key.strip(), voice_id.strip(), model, client or UrllibBinaryClient(), timeout
    def is_available(self): return bool(self.api_key and self.voice_id)
    def _request_parts(self, text: str, *, streaming: bool) -> tuple[str, dict[str, str], bytes]:
        suffix = "/stream" if streaming else ""
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}{suffix}?output_format=mp3_22050_32"
        headers = {"xi-api-key": self.api_key, "Content-Type": "application/json", "Accept": "audio/mpeg"}
        body = json.dumps({"text": text, "model_id": self.model}).encode()
        return url, headers, body
    def iter_audio(self, text: str, *, language: str | None = None) -> Iterator[bytes]:
        if not self.is_available():
            raise ProviderAuthenticationError("elevenlabs is not configured")
        iterator = getattr(self.client, "iter_request", None)
        if iterator is None:
            audio = self.synthesize(text, language=language)
            if audio:
                yield audio
            return
        url, headers, body = self._request_parts(text, streaming=True)
        yield from iterator("POST", url, headers, body, self.timeout, chunk_size=4096)
    def synthesize(self, text: str, *, language: str | None = None) -> bytes:
        if not self.is_available(): raise ProviderAuthenticationError("elevenlabs is not configured")
        url, headers, body = self._request_parts(text, streaming=False)
        response = self.client.request("POST", url, headers, body, self.timeout)
        _check(response, self.name)
        return response.body
