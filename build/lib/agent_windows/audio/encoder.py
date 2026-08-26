from __future__ import annotations

import subprocess

from ..windows_subprocess import hidden_subprocess_kwargs
from dataclasses import dataclass
from typing import BinaryIO, Protocol

from .adaptation import AudioProfile


class AudioEncoder(Protocol):
    def supported_codecs(self) -> set[str]: ...
    def encode(self, pcm_stream: BinaryIO, encoded_stream: BinaryIO, profile: AudioProfile) -> None: ...


@dataclass(frozen=True)
class FFmpegCapabilities:
    executable: str = "ffmpeg"

    def supported_codecs(self) -> set[str]:
        try:
            result = subprocess.run(
                [self.executable, "-hide_banner", "-encoders"], capture_output=True,
                text=True, timeout=10, check=False, **hidden_subprocess_kwargs()
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return set()
        output = result.stdout + result.stderr
        codecs = {"pcm_s16le"}
        if "libopus" in output:
            codecs.add("ogg_opus")
        if "libmp3lame" in output:
            codecs.add("mp3")
        return codecs


def ffmpeg_command(profile: AudioProfile, *, executable: str = "ffmpeg") -> list[str]:
    """Build a streaming stdin/stdout command; execution is owned by a later process adapter."""
    base = [
        executable, "-hide_banner", "-loglevel", "error", "-f", "s16le",
        "-ar", str(profile.sample_rate), "-ac", str(profile.channels), "-i", "pipe:0",
    ]
    if profile.codec == "ogg_opus":
        return base + ["-c:a", "libopus", "-application", "voip", "-b:a", str(profile.bitrate_bps),
                       "-vbr", "on", "-f", "ogg", "pipe:1"]
    if profile.codec == "mp3":
        return base + ["-c:a", "libmp3lame", "-b:a", str(profile.bitrate_bps), "-f", "mp3", "pipe:1"]
    if profile.codec == "pcm_s16le":
        return base + ["-c:a", "pcm_s16le", "-f", "s16le", "pipe:1"]
    raise ValueError(f"unsupported codec: {profile.codec}")
