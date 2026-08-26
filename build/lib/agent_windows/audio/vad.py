from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class VADResult:
    speech: bool
    utterance_started: bool = False
    utterance_ended: bool = False


class VAD(Protocol):
    def process(self, pcm_s16le: bytes, *, timestamp_ms: int) -> VADResult: ...


class EnergyVAD:
    """Dependency-free local baseline. Replaceable by WebRTC/Silero after benchmarking."""

    def __init__(self, *, threshold: float = 0.016, silence_ms: int = 500, frame_ms: int = 20) -> None:
        self.threshold = threshold
        self.silence_ms = silence_ms
        self.frame_ms = frame_ms
        self._active = False
        self._silence = 0

    def process(self, pcm_s16le: bytes, *, timestamp_ms: int) -> VADResult:
        if len(pcm_s16le) % 2:
            raise ValueError("PCM s16le frame must contain complete samples")
        count = len(pcm_s16le) // 2
        samples = struct.unpack(f"<{count}h", pcm_s16le) if count else ()
        rms = math.sqrt(sum(sample * sample for sample in samples) / max(1, count)) / 32768.0
        speech = rms >= self.threshold
        started = speech and not self._active
        ended = False
        if speech:
            self._active = True
            self._silence = 0
        elif self._active:
            self._silence += self.frame_ms
            if self._silence >= self.silence_ms:
                self._active = False
                self._silence = 0
                ended = True
        return VADResult(speech=speech, utterance_started=started, utterance_ended=ended)
