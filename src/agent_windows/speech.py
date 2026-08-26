from __future__ import annotations

import json
import socket
import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .errors import (
    ProviderAuthenticationError,
    ProviderBadResponse,
    ProviderConnectionError,
    ProviderRateLimited,
    ProviderServerError,
    ProviderTimeout,
)
from .http import HTTPResponse, _read_limited


class BinaryHTTPClient(Protocol):
    def request(self, method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float) -> HTTPResponse: ...


class WebSocketConnection(Protocol):
    def send_binary(self, payload: bytes): ...
    def send(self, payload: str): ...
    def recv(self): ...
    def close(self): ...


WebSocketFactory = Callable[[str, Mapping[str, str], float], WebSocketConnection]
PartialCallback = Callable[[str], None]


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


def _websocket_factory(url: str, headers: Mapping[str, str], timeout: float) -> WebSocketConnection:
    try:
        import websocket
    except ImportError as exc:
        raise ProviderConnectionError("websocket-client is required for streaming STT") from exc
    try:
        return websocket.create_connection(url, header=[f"{key}: {value}" for key, value in headers.items()], timeout=timeout)
    except websocket.WebSocketBadStatusException as exc:
        status = getattr(exc, "status_code", None)
        if status in (401, 403):
            raise ProviderAuthenticationError(f"streaming STT authentication failed ({status})") from exc
        if status == 429:
            raise ProviderRateLimited("streaming STT rate limited") from exc
        if status and status >= 500:
            raise ProviderServerError(f"streaming STT server error {status}") from exc
        raise ProviderConnectionError(str(exc)) from exc
    except websocket.WebSocketTimeoutException as exc:
        raise ProviderTimeout(str(exc)) from exc
    except (OSError, websocket.WebSocketException) as exc:
        raise ProviderConnectionError(str(exc)) from exc


def _check(response: HTTPResponse, provider: str):
    if response.status in (401, 403): raise ProviderAuthenticationError(f"{provider} authentication failed")
    if response.status == 429: raise ProviderRateLimited(f"{provider} rate limited")
    if response.status >= 500: raise ProviderServerError(f"{provider} server error {response.status}")
    if not 200 <= response.status < 300: raise ProviderBadResponse(f"{provider} HTTP {response.status}")


@dataclass
class _StreamingResult:
    final_parts: list[str]
    latest: str = ""
    error: Exception | None = None

    def text(self) -> str:
        return " ".join(part.strip() for part in self.final_parts if part.strip()).strip() or self.latest.strip()


def _start_receiver(ws: WebSocketConnection, handler: Callable[[dict], bool], result: _StreamingResult):
    stopped = threading.Event()

    def receive() -> None:
        try:
            while not stopped.is_set():
                raw = ws.recv()
                if raw in (None, b"", ""):
                    break
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                if handler(json.loads(raw)):
                    break
        except Exception as exc:  # websocket implementations expose different exception classes
            if not stopped.is_set():
                result.error = exc

    thread = threading.Thread(target=receive, daemon=True, name="StreamingSTTReceiver")
    thread.start()
    return stopped, thread


class AssemblyAISTT:
    name = "assemblyai"
    supported_codecs = {"ogg_opus", "mp3", "pcm_s16le"}

    def __init__(self, api_key: str, *, client: BinaryHTTPClient | None = None, timeout: float = 45, poll_seconds: float = 1,
                 ws_factory: WebSocketFactory | None = None):
        self.api_key, self.client, self.timeout, self.poll_seconds = api_key.strip(), client or UrllibBinaryClient(), timeout, poll_seconds
        self.ws_factory = ws_factory or _websocket_factory

    def is_available(self): return bool(self.api_key)

    def transcribe_stream(self, frames: Iterable[bytes], *, sample_rate=16000, language="he", on_partial: PartialCallback | None = None) -> str:
        params = {"sample_rate": sample_rate, "speech_model": "whisper-rt", "format_turns": "true"}
        url = f"wss://streaming.assemblyai.com/v3/ws?{urlencode(params)}"
        ws = self.ws_factory(url, {"Authorization": self.api_key}, self.timeout)
        result = _StreamingResult([])
        final_ready = threading.Event()

        def handle(message: dict) -> bool:
            if message.get("type") == "Turn":
                transcript = (message.get("transcript") or "").strip()
                if transcript:
                    result.latest = transcript
                    if on_partial:
                        on_partial(transcript)
                if message.get("end_of_turn") and transcript:
                    result.final_parts = [transcript]
                    final_ready.set()
            elif message.get("type") == "Termination":
                return True
            return False

        stopped, receiver = _start_receiver(ws, handle, result)
        try:
            for frame in frames:
                if frame:
                    ws.send_binary(frame)
            ws.send(json.dumps({"type": "ForceEndpoint"}))
            final_ready.wait(min(4.0, self.timeout))
            ws.send(json.dumps({"type": "Terminate"}))
            receiver.join(timeout=2)
            if result.error and not result.text():
                raise ProviderConnectionError(f"assemblyai streaming receive failed: {result.error}") from result.error
            if not result.text():
                raise ProviderBadResponse("assemblyai streaming returned no transcript")
            return result.text()
        finally:
            stopped.set()
            try: ws.close()
            except Exception: pass

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
            response = self.client.request("GET", f"https://api.assemblyai.com/v2/transcript/{transcript_id}", {"authorization": self.api_key}, None, self.timeout)
            _check(response, self.name)
            data = response.json()
            if data.get("status") == "completed": return data.get("text", "")
            if data.get("status") == "error": raise ProviderBadResponse("assemblyai transcription failed")
            time.sleep(self.poll_seconds)
        raise ProviderTimeout("assemblyai polling timed out")


class DeepgramSTT:
    name = "deepgram"
    supported_codecs = {"ogg_opus", "mp3", "pcm_s16le"}

    def __init__(self, api_key: str, *, model="nova-3", client: BinaryHTTPClient | None = None, timeout=45,
                 ws_factory: WebSocketFactory | None = None):
        self.api_key, self.model, self.client, self.timeout = api_key.strip(), model, client or UrllibBinaryClient(), timeout
        self.ws_factory = ws_factory or _websocket_factory

    def is_available(self): return bool(self.api_key)

    def transcribe_stream(self, frames: Iterable[bytes], *, sample_rate=16000, language="he", on_partial: PartialCallback | None = None) -> str:
        params = {
            "model": self.model, "language": language or "multi", "smart_format": "true",
            "encoding": "linear16", "sample_rate": sample_rate, "channels": 1,
            "interim_results": "true", "endpointing": 300,
        }
        ws = self.ws_factory(f"wss://api.deepgram.com/v1/listen?{urlencode(params)}",
                             {"Authorization": f"Token {self.api_key}"}, self.timeout)
        result = _StreamingResult([])
        final_ready = threading.Event()

        def handle(message: dict) -> bool:
            if message.get("type") != "Results":
                return False
            try:
                transcript = (message["channel"]["alternatives"][0].get("transcript") or "").strip()
            except (KeyError, IndexError, TypeError):
                transcript = ""
            if transcript:
                result.latest = transcript
                if on_partial:
                    on_partial(transcript)
                if message.get("is_final"):
                    result.final_parts.append(transcript)
            if message.get("speech_final") or message.get("from_finalize"):
                final_ready.set()
            return False

        stopped, receiver = _start_receiver(ws, handle, result)
        try:
            for frame in frames:
                if frame:
                    ws.send_binary(frame)
            ws.send(json.dumps({"type": "Finalize"}))
            final_ready.wait(min(4.0, self.timeout))
            ws.send(json.dumps({"type": "CloseStream"}))
            receiver.join(timeout=2)
            if result.error and not result.text():
                raise ProviderConnectionError(f"deepgram streaming receive failed: {result.error}") from result.error
            if not result.text():
                raise ProviderBadResponse("deepgram streaming returned no transcript")
            return result.text()
        finally:
            stopped.set()
            try: ws.close()
            except Exception: pass

    def transcribe(self, audio: bytes, *, content_type="audio/ogg", language: str | None = "he") -> str:
        query = urlencode({"model": self.model, "language": language or "multi", "smart_format": "true"})
        response = self.client.request("POST", f"https://api.deepgram.com/v1/listen?{query}",
                                       {"Authorization": f"Token {self.api_key}", "Content-Type": content_type}, audio, self.timeout)
        _check(response, self.name)
        try: return response.json()["results"]["channels"][0]["alternatives"][0]["transcript"]
        except (KeyError, IndexError, ValueError) as exc: raise ProviderBadResponse("deepgram response malformed") from exc


class STTManager:
    def __init__(self, providers): self.providers = tuple(providers)

    def transcribe_stream(self, frames: Iterable[bytes], *, sample_rate=16000, language="he", on_partial: PartialCallback | None = None):
        available = [provider for provider in self.providers if provider.is_available() and hasattr(provider, "transcribe_stream")]
        if not available:
            raise ProviderConnectionError("No streaming STT provider is configured")
        source = iter(frames)
        buffered: list[bytes] = []
        errors: list[str] = []
        for index, provider in enumerate(available):
            if index == 0:
                def provider_frames():
                    for frame in source:
                        buffered.append(frame)
                        yield frame
                stream = provider_frames()
            else:
                # Replay only what the failed primary already consumed, then continue with the live source.
                # This preserves real-time capture while giving the fallback the beginning of the utterance.
                def replay_then_live():
                    yield from buffered
                    yield from source
                stream = replay_then_live()
            try:
                return provider.transcribe_stream(stream, sample_rate=sample_rate, language=language, on_partial=on_partial)
            except (ProviderAuthenticationError, ProviderConnectionError, ProviderTimeout, ProviderRateLimited, ProviderServerError, ProviderBadResponse) as exc:
                errors.append(f"{provider.name}: {exc}")
        raise ProviderConnectionError("No streaming STT provider succeeded: " + "; ".join(errors))

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
    def synthesize(self, text: str, *, language: str | None = None) -> bytes:
        if not self.is_available(): raise ProviderAuthenticationError("elevenlabs is not configured")
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}?output_format=mp3_22050_32"
        response = self.client.request("POST", url, {"xi-api-key": self.api_key,"Content-Type":"application/json","Accept":"audio/mpeg"},
                                       json.dumps({"text": text, "model_id": self.model}).encode(), self.timeout)
        _check(response, self.name)
        return response.body
