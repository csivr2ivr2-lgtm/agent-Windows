from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from .audio import AudioChunker, EnergyVAD, FFmpegCapabilities, NetworkState, ResilientUploader, ffmpeg_command, profile_for
from .audio.transport import UploadInterrupted
from .errors import ProviderConnectionError, ProviderError


class MicrophoneUnavailable(RuntimeError): pass


class FFmpegMicrophone:
    def __init__(self, device="default", *, ffmpeg="ffmpeg", max_seconds=30, start_timeout=8):
        self.device, self.ffmpeg, self.max_seconds, self.start_timeout = device, ffmpeg, max_seconds, start_timeout

    def capture_pcm_utterance(self, target: Path, vad: EnergyVAD) -> None:
        if shutil.which(self.ffmpeg) is None: raise MicrophoneUnavailable("FFmpeg is not installed or not on PATH")
        if not __import__("sys").platform.startswith("win"): raise MicrophoneUnavailable("voice capture requires Windows")
        command = [self.ffmpeg,"-hide_banner","-loglevel","error","-f","dshow","-i",f"audio={self.device}",
                   "-ac","1","-ar","16000","-f","s16le","pipe:1"]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
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
            try: process.wait(timeout=2)
            except subprocess.TimeoutExpired: process.kill()
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
                result = subprocess.run(ffmpeg_command(profile), stdin=source, stdout=destination, stderr=subprocess.PIPE, timeout=45)
            if result.returncode: raise RuntimeError("FFmpeg audio encoding failed")
            session_id = uuid.uuid4().hex
            if state is NetworkState.OFFLINE:
                with encoded.open("rb") as stream:
                    metadata={"codec":profile.codec,"content_type":profile.content_type,"sample_rate":profile.sample_rate,"channels":profile.channels,"bitrate_bps":profile.bitrate_bps}
                    for chunk in AudioChunker(64*1024,session_id=session_id,chunk_duration_ms=profile.chunk_ms).iter_stream(stream): self.spool.put(chunk,session_metadata=metadata)
                raise ProviderConnectionError("offline: utterance queued locally")
            if self.relay and self.relay.health():
                try:
                    with encoded.open("rb") as stream:
                        chunks = AudioChunker(64*1024,session_id=session_id,chunk_duration_ms=profile.chunk_ms).iter_stream(stream)
                        result, _ = ResilientUploader(self.relay).upload(chunks, metadata=profile.__dict__)
                    if result.get("transcript") is not None: return result["transcript"]
                except (ProviderError, UploadInterrupted, ConnectionError, TimeoutError):
                    metadata={"codec":profile.codec,"content_type":profile.content_type,"sample_rate":profile.sample_rate,"channels":profile.channels,"bitrate_bps":profile.bitrate_bps}
                    with encoded.open("rb") as stream:
                        for chunk in AudioChunker(64*1024,session_id=session_id,chunk_duration_ms=profile.chunk_ms).iter_stream(stream): self.spool.put(chunk,session_metadata=metadata)
            if not self.direct_allowed:
                raise ProviderConnectionError("relay unavailable and direct STT is disabled")
            data = encoded.read_bytes()
            return self.stt.transcribe(data, content_type=profile.content_type, language="he")

    def speak(self, text: str) -> None:
        if not self.tts or not self.tts.is_available(): return
        audio = self.tts.synthesize(text, language="he")
        player = shutil.which("ffplay")
        if not player: return
        with tempfile.NamedTemporaryFile(suffix=".mp3") as file:
            file.write(audio); file.flush()
            subprocess.run([player,"-nodisp","-autoexit","-loglevel","error",file.name], check=False)
