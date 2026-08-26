from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from .audio import AudioChunker, EnergyVAD, FFmpegCapabilities, NetworkState, ResilientUploader, ffmpeg_command, profile_for
from .audio.transport import UploadInterrupted
from .errors import ProviderConnectionError, ProviderError
from .windows_subprocess import hidden_subprocess_kwargs


logger = logging.getLogger(__name__)


class MicrophoneUnavailable(RuntimeError): pass


def _audio_metadata(profile) -> dict:
    return {
        "codec": profile.codec,
        "content_type": profile.content_type,
        "sample_rate": profile.sample_rate,
        "channels": profile.channels,
        "bitrate_bps": profile.bitrate_bps,
    }


def _spool_file(spool, encoded: Path, session_id: str, profile) -> None:
    with encoded.open("rb") as stream:
        chunks = AudioChunker(
            64 * 1024, session_id=session_id, chunk_duration_ms=profile.chunk_ms
        ).iter_stream(stream)
        for chunk in chunks:
            spool.put(chunk, session_metadata=_audio_metadata(profile))


class FFmpegMicrophone:
    def __init__(self, device="default", *, ffmpeg="ffmpeg", max_seconds=30, start_timeout=8):
        self.device, self.ffmpeg, self.max_seconds, self.start_timeout = device, ffmpeg, max_seconds, start_timeout

    def capture_pcm_utterance(self, target: Path, vad: EnergyVAD) -> None:
        if shutil.which(self.ffmpeg) is None: raise MicrophoneUnavailable("FFmpeg is not installed or not on PATH")
        if not __import__("sys").platform.startswith("win"): raise MicrophoneUnavailable("voice capture requires Windows")
        command = [self.ffmpeg,"-hide_banner","-loglevel","error","-f","dshow","-i",f"audio={self.device}",
                   "-ac","1","-ar","16000","-f","s16le","pipe:1"]
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            **hidden_subprocess_kwargs(),
        )
        started, speech_seen, started_at = False, False, time.monotonic()
        try:
            with target.open("wb") as output:
                while time.monotonic() - started_at < self.max_seconds:
                    frame = process.stdout.read(640) if process.stdout else b""
                    if len(frame) < 640: break
                    result = vad.process(frame, timestamp_ms=int((time.monotonic()-started_at)*1000))
                    if result.utterance_started: started = speech_seen = True
                    if started: output.write(frame)
                    if result.utterance_ended: break
                    if not speech_seen and time.monotonic() - started_at > self.start_timeout: raise MicrophoneUnavailable("no speech detected")
        finally:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        if not speech_seen or target.stat().st_size == 0: raise MicrophoneUnavailable("no microphone audio captured")


class VoiceService:
    def __init__(self, *, microphone, stt, tts, relay=None, network_monitor=None, spool=None, direct_allowed=True):
        self.microphone, self.stt, self.tts, self.relay = microphone, stt, tts, relay
        self.network, self.spool = network_monitor, spool
        self.direct_allowed = direct_allowed

    def listen(self) -> str:
        state = self.network.state if self.network else NetworkState.GOOD
        capabilities = FFmpegCapabilities().supported_codecs()
        relay_codecs = {"ogg_opus", "mp3", "pcm_s16le"} if self.relay and self.relay.is_available() else capabilities
        stt_codecs = set.intersection(*(set(p.supported_codecs) for p in self.stt.providers if p.is_available())) if any(p.is_available() for p in self.stt.providers) else set()
        supported = capabilities & (relay_codecs | stt_codecs)
        profile = profile_for(state, supported)
        vad = EnergyVAD(threshold=profile.vad_threshold, silence_ms=profile.vad_silence_ms)
        with tempfile.TemporaryDirectory() as directory:
            pcm, encoded = Path(directory)/"utterance.pcm", Path(directory)/"utterance.audio"
            self.microphone.capture_pcm_utterance(pcm, vad)
            with pcm.open("rb") as source, encoded.open("wb") as destination:
                result = subprocess.run(
                    ffmpeg_command(profile),
                    stdin=source,
                    stdout=destination,
                    stderr=subprocess.PIPE,
                    timeout=45,
                    **hidden_subprocess_kwargs(),
                )
            if result.returncode: raise RuntimeError("FFmpeg audio encoding failed")
            session_id = uuid.uuid4().hex
            if state is NetworkState.OFFLINE:
                _spool_file(self.spool, encoded, session_id, profile)
                raise ProviderConnectionError("offline: utterance queued locally")
            if self.relay and self.relay.health():
                try:
                    with encoded.open("rb") as stream:
                        chunks = AudioChunker(64*1024,session_id=session_id,chunk_duration_ms=profile.chunk_ms).iter_stream(stream)
                        result, _ = ResilientUploader(self.relay).upload(chunks, metadata=profile.__dict__)
                    if result.get("transcript") is not None: return result["transcript"]
                except (ProviderError, UploadInterrupted, ConnectionError, TimeoutError):
                    _spool_file(self.spool, encoded, session_id, profile)
            if not self.direct_allowed:
                raise ProviderConnectionError("relay unavailable and direct STT is disabled")
            data = encoded.read_bytes()
            return self.stt.transcribe(data, content_type=profile.content_type, language="he")

    def speak(self, text: str) -> None:
        player = shutil.which("ffplay")
        if not player:
            return

        # Prefer relay streaming: playback starts as soon as the first MP3 bytes arrive,
        # instead of waiting for the entire ElevenLabs response to download.
        if self.relay and self.relay.is_available() and hasattr(self.relay, "iter_tts"):
            process = None
            streamed = False
            try:
                process = subprocess.Popen(
                    [player, "-nodisp", "-autoexit", "-loglevel", "error", "-i", "pipe:0"],
                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    **hidden_subprocess_kwargs(),
                )
                for chunk in self.relay.iter_tts(text, language="he"):
                    if not chunk:
                        continue
                    streamed = True
                    if process.stdin is None:
                        break
                    process.stdin.write(chunk)
                    process.stdin.flush()
                if process.stdin is not None:
                    process.stdin.close()
                process.wait(timeout=30)
                if streamed:
                    return
            except (ProviderError, OSError, subprocess.SubprocessError) as exc:
                logger.warning("Relay TTS streaming failed: %s", exc)
            finally:
                if process is not None and process.poll() is None:
                    process.kill()
                    process.wait(timeout=2)
            if streamed:
                return

        if not self.tts or not self.tts.is_available():
            return
        audio = self.tts.synthesize(text, language="he")
        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "speech.mp3"
            audio_path.write_bytes(audio)
            subprocess.run(
                [player, "-nodisp", "-autoexit", "-loglevel", "error", str(audio_path)],
                check=False,
                **hidden_subprocess_kwargs(),
            )
