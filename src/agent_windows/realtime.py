from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


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
        return {k: round((b-a)*1000, 1) for k, (a, b) in pairs.items() if a is not None and b is not None}

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


class LiveKitSessionAdapter:
    """Optional LiveKit Agents boundary; local desktop capture/playback stays authoritative."""
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
