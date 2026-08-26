from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, Protocol
from urllib.parse import urlencode

from .errors import (
    ProviderAuthenticationError,
    ProviderBadResponse,
    ProviderConnectionError,
    ProviderPermissionError,
    ProviderRateLimited,
    ProviderServerError,
)


class WebSocketConnection(Protocol):
    def send(self, message: str | bytes) -> None: ...
    def recv(self, timeout: float | None = None) -> str | bytes: ...
    def close(self) -> None: ...


WebSocketFactory = Callable[[str, dict[str, str], float], WebSocketConnection]


@dataclass(frozen=True)
class TranscriptEvent:
    provider: str
    text: str = ""
    is_final: bool = False
    speech_started: bool = False


def _default_websocket_factory(
    url: str,
    headers: dict[str, str],
    timeout: float,
) -> WebSocketConnection:
    try:
        from websockets.sync.client import connect
    except ImportError as exc:
        raise ProviderConnectionError(
            "streaming STT requires websockets; install the locked runtime dependencies"
        ) from exc
    try:
        return connect(
            url,
            additional_headers=headers,
            open_timeout=timeout,
            close_timeout=min(5.0, timeout),
            ping_interval=20,
            ping_timeout=20,
            max_size=2 * 1024 * 1024,
            compression=None,
        )
    except Exception as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status == 401:
            raise ProviderAuthenticationError("streaming STT authentication failed (HTTP 401)") from exc
        if status == 403:
            raise ProviderPermissionError("streaming STT permission denied (HTTP 403)") from exc
        if status == 429:
            raise ProviderRateLimited("streaming STT rate limited") from exc
        if isinstance(status, int) and status >= 500:
            raise ProviderServerError(f"streaming STT server error (HTTP {status})") from exc
        raise ProviderConnectionError(f"streaming STT WebSocket connection failed: {exc}") from exc


class _JSONWebSocketSession:
    provider = "unknown"

    def __init__(self, connection: WebSocketConnection):
        self.connection = connection
        self.closed = False

    def send_audio(self, pcm16: bytes) -> None:
        if self.closed:
            raise ProviderConnectionError(f"{self.provider} streaming session is closed")
        if pcm16:
            try:
                self.connection.send(pcm16)
            except Exception as exc:
                raise ProviderConnectionError(f"{self.provider} streaming send failed: {exc}") from exc

    def _recv_json(self, timeout: float | None) -> dict:
        try:
            raw = self.connection.recv(timeout=timeout)
        except TimeoutError:
            raise
        except Exception as exc:
            raise ProviderConnectionError(f"{self.provider} streaming receive failed: {exc}") from exc
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="strict")
        try:
            data = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ProviderBadResponse(f"{self.provider} streaming response is not valid JSON") from exc
        if not isinstance(data, dict):
            raise ProviderBadResponse(f"{self.provider} streaming response must be an object")
        return data

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.connection.close()
        except Exception:
            pass


class AssemblyAIStreamingSession(_JSONWebSocketSession):
    provider = "assemblyai"

    def recv_event(self, timeout: float | None = None) -> TranscriptEvent | None:
        data = self._recv_json(timeout)
        kind = data.get("type")
        if kind == "SpeechStarted":
            return TranscriptEvent(self.provider, speech_started=True)
        if kind == "Turn":
            return TranscriptEvent(
                self.provider,
                str(data.get("transcript") or ""),
                bool(data.get("end_of_turn")),
            )
        if kind == "Termination":
            self.closed = True
            return None
        return None

    def force_endpoint(self) -> None:
        if not self.closed:
            self.connection.send(json.dumps({"type": "ForceEndpoint"}))

    def close(self) -> None:
        if self.closed:
            return
        try:
            self.connection.send(json.dumps({"type": "Terminate"}))
        except Exception:
            pass
        super().close()


class DeepgramStreamingSession(_JSONWebSocketSession):
    provider = "deepgram"

    def recv_event(self, timeout: float | None = None) -> TranscriptEvent | None:
        data = self._recv_json(timeout)
        kind = data.get("type")
        if kind == "SpeechStarted":
            return TranscriptEvent(self.provider, speech_started=True)
        if kind == "Results":
            try:
                transcript = data["channel"]["alternatives"][0].get("transcript") or ""
            except (KeyError, IndexError, TypeError) as exc:
                raise ProviderBadResponse("deepgram streaming response malformed") from exc
            return TranscriptEvent(
                self.provider,
                str(transcript),
                bool(data.get("speech_final") or data.get("from_finalize")),
            )
        if kind == "UtteranceEnd":
            return TranscriptEvent(self.provider, is_final=True)
        return None

    def keep_alive(self) -> None:
        if not self.closed:
            self.connection.send(json.dumps({"type": "KeepAlive"}))

    def force_endpoint(self) -> None:
        if not self.closed:
            self.connection.send(json.dumps({"type": "Finalize"}))

    def close(self) -> None:
        if self.closed:
            return
        try:
            self.connection.send(json.dumps({"type": "CloseStream"}))
        except Exception:
            pass
        super().close()


class AssemblyAIStreamingSTT:
    """AssemblyAI v3 raw WebSocket streaming.

    Hebrew uses AssemblyAI Whisper Streaming because it explicitly supports Hebrew.
    Universal-3.5 Pro is used for languages in its realtime language set.
    """

    name = "assemblyai"

    def __init__(
        self,
        api_key: str,
        *,
        timeout: float = 10.0,
        websocket_factory: WebSocketFactory | None = None,
    ):
        self.api_key = api_key.strip()
        self.timeout = timeout
        self.websocket_factory = websocket_factory or _default_websocket_factory

    def is_available(self) -> bool:
        return bool(self.api_key)

    def open(self, *, language: str = "he", sample_rate: int = 16000) -> AssemblyAIStreamingSession:
        if not self.api_key:
            raise ProviderAuthenticationError("assemblyai streaming is not configured")
        if sample_rate != 16000:
            raise ValueError("assemblyai streaming production path requires 16 kHz PCM16")
        if language.casefold() in {"he", "he-il", "hebrew"}:
            params = {
                "sample_rate": sample_rate,
                "speech_model": "whisper-rt",
                "format_turns": "true",
                "language_detection": "true",
            }
        else:
            params = {
                "sample_rate": sample_rate,
                "speech_model": "universal-3-5-pro",
                "prompt": f"Transcribe {language}.",
            }
        url = "wss://streaming.assemblyai.com/v3/ws?" + urlencode(params)
        connection = self.websocket_factory(url, {"Authorization": self.api_key}, self.timeout)
        return AssemblyAIStreamingSession(connection)


class DeepgramStreamingSTT:
    name = "deepgram"

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "nova-3",
        timeout: float = 10.0,
        endpointing_ms: int = 300,
        websocket_factory: WebSocketFactory | None = None,
    ):
        self.api_key = api_key.strip()
        self.model = model
        self.timeout = timeout
        self.endpointing_ms = endpointing_ms
        self.websocket_factory = websocket_factory or _default_websocket_factory

    def is_available(self) -> bool:
        return bool(self.api_key)

    def open(self, *, language: str = "he", sample_rate: int = 16000) -> DeepgramStreamingSession:
        if not self.api_key:
            raise ProviderAuthenticationError("deepgram streaming is not configured")
        params = {
            "model": self.model,
            "language": language,
            "encoding": "linear16",
            "sample_rate": sample_rate,
            "channels": 1,
            "interim_results": "true",
            "smart_format": "true",
            "vad_events": "true",
            "endpointing": self.endpointing_ms,
            "utterance_end_ms": 1000,
        }
        url = "wss://api.deepgram.com/v1/listen?" + urlencode(params)
        connection = self.websocket_factory(
            url,
            {"Authorization": f"Token {self.api_key}"},
            self.timeout,
        )
        return DeepgramStreamingSession(connection)


class ManagedStreamingSTTSession:
    """Thread-safe sequential STT failover for one long-lived call.

    Exactly one provider connection is active at a time. A transient connection/server
    failure gets one bounded reconnect on the same provider before advancing to the next
    configured provider. Authentication, permission and rate-limit failures skip directly
    to the fallback provider.
    """

    def __init__(
        self,
        providers,
        *,
        language: str,
        sample_rate: int,
        max_reconnects_per_provider: int = 1,
    ) -> None:
        import threading

        self.providers = tuple(providers)
        self.language = language
        self.sample_rate = sample_rate
        self.max_reconnects_per_provider = max(0, max_reconnects_per_provider)
        self._state_lock = threading.Lock()
        self._reconnect_lock = threading.Lock()
        self._provider_index = -1
        self._reconnects: dict[int, int] = {}
        self._session = None
        self._closed = False
        self._open_initial()

    @property
    def provider(self) -> str:
        with self._state_lock:
            session = self._session
        return str(getattr(session, "provider", "unknown"))

    def _available_indices(self):
        return [index for index, provider in enumerate(self.providers) if provider.is_available()]

    def _open_provider(self, index: int):
        provider = self.providers[index]
        return provider.open(language=self.language, sample_rate=self.sample_rate)

    def _open_initial(self) -> None:
        failures: list[str] = []
        for index in self._available_indices():
            try:
                session = self._open_provider(index)
            except (
                ProviderAuthenticationError,
                ProviderConnectionError,
                ProviderPermissionError,
                ProviderRateLimited,
                ProviderServerError,
            ) as exc:
                failures.append(f"{self.providers[index].name}: {exc}")
                continue
            with self._state_lock:
                self._provider_index = index
                self._session = session
            return
        detail = "; ".join(failures) if failures else "no configured streaming STT provider"
        raise ProviderConnectionError("No streaming STT provider succeeded: " + detail)

    def _snapshot(self):
        with self._state_lock:
            return self._session, self._provider_index, self._closed

    def _replace_session(self, failed_session, error: BaseException) -> None:
        with self._reconnect_lock:
            current, index, closed = self._snapshot()
            if closed:
                raise ProviderConnectionError("streaming STT session is closed")
            if current is not failed_session:
                return

            try:
                failed_session.close()
            except Exception:
                pass

            transient = isinstance(error, (ProviderConnectionError, ProviderServerError))
            attempts = self._reconnects.get(index, 0)
            candidates: list[int] = []
            if transient and index >= 0 and attempts < self.max_reconnects_per_provider:
                self._reconnects[index] = attempts + 1
                candidates.append(index)
            candidates.extend(i for i in self._available_indices() if i > index)
            candidates.extend(i for i in self._available_indices() if i < index)

            failures = [f"{getattr(failed_session, 'provider', 'unknown')}: {error}"]
            seen: set[int] = set()
            for candidate in candidates:
                if candidate in seen:
                    continue
                seen.add(candidate)
                try:
                    replacement = self._open_provider(candidate)
                except (
                    ProviderAuthenticationError,
                    ProviderConnectionError,
                    ProviderPermissionError,
                    ProviderRateLimited,
                    ProviderServerError,
                ) as exc:
                    failures.append(f"{self.providers[candidate].name}: {exc}")
                    continue
                with self._state_lock:
                    if self._closed:
                        replacement.close()
                        raise ProviderConnectionError("streaming STT session is closed")
                    self._provider_index = candidate
                    self._session = replacement
                return

            with self._state_lock:
                self._session = None
            raise ProviderConnectionError(
                "Streaming STT recovery exhausted: " + "; ".join(failures)
            )

    def send_audio(self, pcm16: bytes) -> None:
        session, _index, closed = self._snapshot()
        if closed or session is None:
            raise ProviderConnectionError("streaming STT session is closed")
        try:
            session.send_audio(pcm16)
        except (ProviderConnectionError, ProviderServerError) as exc:
            self._replace_session(session, exc)
            replacement, _index, closed = self._snapshot()
            if closed or replacement is None:
                raise ProviderConnectionError("streaming STT session is closed")
            replacement.send_audio(pcm16)

    def recv_event(self, timeout: float | None = None) -> TranscriptEvent | None:
        session, _index, closed = self._snapshot()
        if closed or session is None:
            raise ProviderConnectionError("streaming STT session is closed")
        try:
            return session.recv_event(timeout=timeout)
        except TimeoutError:
            raise
        except (
            ProviderAuthenticationError,
            ProviderConnectionError,
            ProviderPermissionError,
            ProviderRateLimited,
            ProviderServerError,
        ) as exc:
            self._replace_session(session, exc)
            return None

    def force_endpoint(self) -> None:
        session, _index, closed = self._snapshot()
        if closed or session is None:
            return
        try:
            session.force_endpoint()
        except (ProviderConnectionError, ProviderServerError) as exc:
            self._replace_session(session, exc)

    def close(self) -> None:
        with self._reconnect_lock:
            with self._state_lock:
                if self._closed:
                    return
                self._closed = True
                session = self._session
                self._session = None
            if session is not None:
                session.close()


class StreamingSTTManager:
    """Opens one provider at a time and keeps bounded sequential failover active."""

    def __init__(self, providers, *, max_reconnects_per_provider: int = 1):
        self.providers = tuple(providers)
        self.max_reconnects_per_provider = max(0, max_reconnects_per_provider)

    def open(self, *, language: str = "he", sample_rate: int = 16000):
        return ManagedStreamingSTTSession(
            self.providers,
            language=language,
            sample_rate=sample_rate,
            max_reconnects_per_provider=self.max_reconnects_per_provider,
        )
