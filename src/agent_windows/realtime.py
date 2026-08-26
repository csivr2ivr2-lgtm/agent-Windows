from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from .audio import EnergyVAD
from .errors import ProviderConnectionError

logger = logging.getLogger(__name__)


class RealtimeState(str, Enum):
    CONNECTING = "connecting"
    LISTENING = "listening"
    USER_SPEAKING = "user_speaking"
    THINKING = "thinking"
    SPEAKING = "speaking"
    INTERRUPTING = "interrupting"
    ERROR = "error"
    ENDING = "ending"


@dataclass
class LatencyMetrics:
    speech_end: float | None = None
    transcript_ready: float | None = None
    first_llm_token: float | None = None
    first_audio_byte: float | None = None
    first_audible: float | None = None
    barge_in_detected: float | None = None
    playback_stopped: float | None = None

    def mark(self, name: str) -> None:
        setattr(self, name, time.monotonic())

    def milliseconds(self) -> dict[str, float]:
        pairs = {
            "speech_end_to_transcript_ready": (self.speech_end, self.transcript_ready),
            "transcript_ready_to_first_llm_token": (self.transcript_ready, self.first_llm_token),
            "first_llm_token_to_first_audio_byte": (self.first_llm_token, self.first_audio_byte),
            "speech_end_to_first_audible_response": (self.speech_end, self.first_audible),
            "barge_in_to_playback_stopped": (self.barge_in_detected, self.playback_stopped),
        }
        return {
            key: round((end - start) * 1000, 1)
            for key, (start, end) in pairs.items()
            if start is not None and end is not None
        }

    def log(self) -> None:
        values = self.milliseconds()
        if values:
            logger.info("voice_latency_ms=%s", values)


@dataclass
class CancellationScope:
    event: threading.Event = field(default_factory=threading.Event)

    def cancel(self) -> None:
        self.event.set()

    @property
    def cancelled(self) -> bool:
        return self.event.is_set()


class LocalRealtimeSession:
    """Persistent local voice session with streaming STT, LLM, TTS and barge-in.

    Audio capture and STT stay alive for the duration of the call. The receive loop is
    independent from response playback so user speech can interrupt the agent.
    """

    def __init__(
        self,
        runtime,
        *,
        status_callback: Callable[[RealtimeState], None] | None = None,
        frame_ms: int = 50,
        barge_in_frames: int = 3,
        vad_factory: Callable[[], EnergyVAD] | None = None,
        barge_vad_factory: Callable[[], EnergyVAD] | None = None,
    ) -> None:
        self.runtime = runtime
        self.status_callback = status_callback or (lambda _state: None)
        self.frame_ms = frame_ms
        self.barge_in_frames = max(1, barge_in_frames)
        self.vad_factory = vad_factory or (
            lambda: EnergyVAD(threshold=0.016, silence_ms=500, frame_ms=frame_ms)
        )
        self.barge_vad_factory = barge_vad_factory or (
            lambda: EnergyVAD(threshold=0.045, silence_ms=300, frame_ms=frame_ms)
        )
        self.metrics = LatencyMetrics()
        self.state = RealtimeState.CONNECTING
        self._state_lock = threading.Lock()
        self._response_lock = threading.Lock()
        self._response_scope: CancellationScope | None = None
        self._response_thread: threading.Thread | None = None
        self._barge_frames_seen = 0
        self._barge_accepted = False
        self._session_error: BaseException | None = None

    def _set_state(self, state: RealtimeState) -> None:
        with self._state_lock:
            self.state = state
        self.status_callback(state)

    def _response_active(self) -> bool:
        with self._response_lock:
            thread = self._response_thread
        return bool(thread and thread.is_alive())

    def _cancel_response_for_barge_in(self) -> None:
        if not self._response_active() or self._barge_accepted:
            return
        self._barge_accepted = True
        self.metrics.mark("barge_in_detected")
        self._set_state(RealtimeState.INTERRUPTING)
        with self._response_lock:
            scope = self._response_scope
        if scope:
            scope.cancel()
        self.runtime.voice.cancel_playback()
        self.metrics.mark("playback_stopped")
        self.metrics.log()
        self._set_state(RealtimeState.USER_SPEAKING)

    def _start_response(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if self._response_active():
            self._cancel_response_for_barge_in()
        scope = CancellationScope()
        with self._response_lock:
            self._response_scope = scope
        self._barge_accepted = False
        self.metrics.mark("transcript_ready")

        def worker() -> None:
            first_token = True
            first_audio = True
            self._set_state(RealtimeState.THINKING)

            def chunks():
                nonlocal first_token
                for chunk in self.runtime.stream_text(text, cancel_event=scope.event):
                    if scope.cancelled:
                        return
                    if first_token and chunk:
                        first_token = False
                        self.metrics.mark("first_llm_token")
                        self._set_state(RealtimeState.SPEAKING)
                    yield chunk

            def audio_started() -> None:
                nonlocal first_audio
                if first_audio:
                    first_audio = False
                    self.metrics.mark("first_audio_byte")
                    self.metrics.mark("first_audible")

            try:
                self.runtime.voice.speak_chunks(
                    chunks(), cancel_event=scope.event, on_audio_start=audio_started
                )
            except Exception:
                logger.exception("Realtime response failed")
                self._set_state(RealtimeState.ERROR)
            finally:
                self.metrics.log()
                with self._response_lock:
                    if self._response_scope is scope:
                        self._response_scope = None
                        self._response_thread = None
                if not scope.cancelled and self.state is not RealtimeState.ERROR:
                    self._set_state(RealtimeState.LISTENING)

        thread = threading.Thread(target=worker, daemon=True, name="AiAharonRealtimeResponse")
        with self._response_lock:
            self._response_thread = thread
        thread.start()

    def _receiver(self, stt_session, keep_running: Callable[[], bool]) -> None:
        while keep_running():
            try:
                event = stt_session.recv_event(timeout=0.5)
            except TimeoutError:
                continue
            except ProviderConnectionError as exc:
                self._session_error = exc
                return
            if event is None:
                continue
            if event.speech_started and not self._response_active():
                self._set_state(RealtimeState.USER_SPEAKING)
            if not event.is_final:
                continue
            text = event.text.strip()
            if not text:
                continue
            if self._response_active() and not self._barge_accepted:
                logger.debug("Ignoring final STT during playback without local barge-in")
                continue
            self._start_response(text)

    def run(self, keep_running: Callable[[], bool]) -> None:
        self._set_state(RealtimeState.CONNECTING)
        stt_session = self.runtime.streaming_stt.open(language="he", sample_rate=16000)
        microphone = self.runtime.voice.microphone.open_pcm_stream(frame_ms=self.frame_ms)
        normal_vad = self.vad_factory()
        barge_vad = self.barge_vad_factory()
        receiver = threading.Thread(
            target=self._receiver,
            args=(stt_session, keep_running),
            daemon=True,
            name="AiAharonStreamingSTT",
        )
        receiver.start()
        self._set_state(RealtimeState.LISTENING)
        started_at = time.monotonic()
        try:
            while keep_running():
                if self._session_error:
                    raise ProviderConnectionError(str(self._session_error))
                frame = microphone.read_frame()
                if not frame:
                    raise ProviderConnectionError("persistent microphone stream ended")
                stt_session.send_audio(frame)
                timestamp_ms = int((time.monotonic() - started_at) * 1000)
                if self._response_active():
                    result = barge_vad.process(frame, timestamp_ms=timestamp_ms)
                    self._barge_frames_seen = self._barge_frames_seen + 1 if result.speech else 0
                    if self._barge_frames_seen >= self.barge_in_frames:
                        self._cancel_response_for_barge_in()
                    if result.utterance_ended:
                        self.metrics.mark("speech_end")
                        self._barge_frames_seen = 0
                        stt_session.force_endpoint()
                else:
                    self._barge_frames_seen = 0
                    result = normal_vad.process(frame, timestamp_ms=timestamp_ms)
                    if result.utterance_started:
                        self._set_state(RealtimeState.USER_SPEAKING)
                    if result.utterance_ended:
                        self.metrics.mark("speech_end")
                        stt_session.force_endpoint()
                        self._set_state(RealtimeState.LISTENING)
        finally:
            self._set_state(RealtimeState.ENDING)
            with self._response_lock:
                scope = self._response_scope
                response_thread = self._response_thread
            if scope:
                scope.cancel()
            self.runtime.voice.cancel_playback()
            microphone.close()
            stt_session.close()
            receiver.join(timeout=2)
            if response_thread:
                response_thread.join(timeout=2)


class LiveKitSessionAdapter:
    """LiveKit availability boundary kept separate from the local realtime engine."""

    def __init__(self, enabled: bool = False):
        self.enabled = enabled

    def available(self) -> bool:
        if not self.enabled:
            return False
        try:
            import livekit.agents  # noqa: F401
            return True
        except ImportError:
            return False
