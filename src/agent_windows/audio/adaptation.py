from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


class NetworkState(str, Enum):
    GOOD = "GOOD"
    DEGRADED = "DEGRADED"
    POOR = "POOR"
    OFFLINE = "OFFLINE"


@dataclass(frozen=True)
class AudioProfile:
    codec: str
    content_type: str
    sample_rate: int
    channels: int
    bitrate_bps: int
    chunk_ms: int
    vad_threshold: float
    vad_silence_ms: int
    store_offline: bool = False


_PROFILES = {
    NetworkState.GOOD: AudioProfile("ogg_opus", "audio/ogg; codecs=opus", 16000, 1, 24000, 100, 0.014, 600),
    NetworkState.DEGRADED: AudioProfile("ogg_opus", "audio/ogg; codecs=opus", 16000, 1, 16000, 80, 0.016, 500),
    NetworkState.POOR: AudioProfile("ogg_opus", "audio/ogg; codecs=opus", 16000, 1, 12000, 60, 0.019, 400),
    NetworkState.OFFLINE: AudioProfile("ogg_opus", "audio/ogg; codecs=opus", 16000, 1, 12000, 60, 0.019, 400, True),
}


def profile_for(state: NetworkState, supported_codecs: Iterable[str] = ("ogg_opus",)) -> AudioProfile:
    """Select quality only from codecs supported end-to-end by encoder, transport and STT."""
    base = _PROFILES[state]
    supported = set(supported_codecs)
    if base.codec in supported:
        return base
    if "mp3" in supported:
        return AudioProfile("mp3", "audio/mpeg", 16000, 1, max(16000, base.bitrate_bps), base.chunk_ms,
                            base.vad_threshold, base.vad_silence_ms, base.store_offline)
    if "pcm_s16le" in supported:
        return AudioProfile("pcm_s16le", "audio/L16", 16000, 1, 256000, base.chunk_ms,
                            base.vad_threshold, base.vad_silence_ms, base.store_offline)
    raise ValueError("No mutually supported speech codec")
