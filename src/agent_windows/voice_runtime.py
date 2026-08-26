from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import time
import uuid
import threading
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

    def iter_pcm_utterance(self, vad: EnergyVAD):
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
        started = speech_seen = False
        started_at = time.monotonic()
        try:
            while time.monotonic() - started_at < self.max_seconds:
                frame = process.stdout.read(1600) if process.stdout else b""  # 50 ms PCM16 @ 16 kHz mono
                if len(frame) < 1600:
                    break
                result = vad.process(frame, timestamp_ms=int((time.monotonic()-started_at)*1000))
                if result.utterance_started:
                    started = speech_seen = True
                if started:
                    yield frame
                if result.utterance_ended:
                    break
                if not speech_seen and time.monotonic() - started_at > self.start_timeout:
                    raise MicrophoneUnavailable("no speech detected")
        finally:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        if not speech_seen:
            raise MicrophoneUnavailable("no microphone audio captured")

    def wait_for_speech(self, stop_event: threading.Event, *, threshold=900.0, timeout=30.0) -> bool:
        if shutil.which(self.ffmpeg) is None or not __import__("sys").platform.startswith("win"):
            return False
        command = [self.ffmpeg,"-hide_banner","-loglevel","error","-f","dshow","-i",f"audio={self.device}",
                   "-ac","1","-ar","16000","-f","s16le","pipe:1"]
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **hidden_subprocess_kwargs()
        )
        vad = EnergyVAD(threshold=threshold, silence_ms=180)
        started_at = time.monotonic()
        try:
            while not stop_event.is_set() and time.monotonic() - started_at < timeout:
                frame = process.stdout.read(1600) if process.stdout else b""
                if len(frame) < 1600:
                    return False
                if vad.process(frame, timestamp_ms=int((time.monotonic()-started_at)*1000)).utterance_started:
                    return True
            return False
        finally:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)

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
        self._playback_lock = threading.Lock()
        self._playback_process = None

    def listen(self) -> str:
        state = self.network.state if self.network else NetworkState.GOOD
        capabilities = FFmpegCapabilities().supported_codecs()
        relay_codecs = {"ogg_opus", "mp3", "pcm_s16le"} if self.relay and self.relay.is_available() else capabilities
        stt_codecs = set.intersection(*(set(p.supported_codecs) for p in self.stt.providers if p.is_available())) if any(p.is_available() for p in self.stt.providers) else set()
        supported = capabilities & (relay_codecs | stt_codecs)
        profile = profile_for(state, supported)
        vad = EnergyVAD(threshold=profile.vad_threshold, silence_ms=profile.vad_silence_ms)
        if state is not NetworkState.OFFLINE and self.direct_allowed and hasattr(self.stt, "transcribe_stream"):
            frames = self.microphone.iter_pcm_utterance(vad)
            return self.stt.transcribe_stream(frames, sample_rate=16000, language="he")
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

    def cancel_playback(self) -> None:
        with self._playback_lock:
            process = self._playback_process
        if process is not None and process.poll() is None:
            process.kill()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass

    def _start_barge_in_monitor(self, playback_cancel: threading.Event, monitor_stop: threading.Event):
        if not hasattr(self.microphone, "wait_for_speech"):
            return None

        def monitor() -> None:
            try:
                if monitor_stop.wait(0.25):
                    return
                if self.microphone.wait_for_speech(monitor_stop):
                    playback_cancel.set()
                    self.cancel_playback()
            except Exception:
                logger.debug("Barge-in monitor failed", exc_info=True)

        thread = threading.Thread(target=monitor, daemon=True, name="VoiceBargeIn")
        thread.start()
        return thread

    def speak(self, text: str, *, cancel_event=None) -> None:
        player = shutil.which("ffplay")
        if not player:
            return

        playback_cancel = threading.Event()
        monitor_stop = threading.Event()
        monitor_thread = None

        def cancelled() -> bool:
            return playback_cancel.is_set() or bool(cancel_event is not None and cancel_event.is_set())

        if cancelled():
            return
        if self.relay and self.relay.is_available() and hasattr(self.relay, "iter_tts"):
            process = None
            streamed = False
            try:
                process = subprocess.Popen(
                    [player, "-nodisp", "-autoexit", "-loglevel", "error", "-i", "pipe:0"],
                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    **hidden_subprocess_kwargs(),
                )
                with self._playback_lock:
                    self._playback_process = process
                monitor_thread = self._start_barge_in_monitor(playback_cancel, monitor_stop)
                for chunk in self.relay.iter_tts(text, language="he"):
                    if cancelled():
                        process.kill()
                        break
                    if not chunk:
                        continue
                    streamed = True
                    if process.stdin is None:
                        break
                    process.stdin.write(chunk)
                    process.stdin.flush()
                if process.poll() is None and process.stdin is not None:
                    process.stdin.close()
                if process.poll() is None:
                    process.wait(timeout=30)
                if streamed or cancelled():
                    return
            except (ProviderError, OSError, subprocess.SubprocessError) as exc:
                logger.warning("Relay TTS streaming failed: %s", exc)
            finally:
                monitor_stop.set()
                if monitor_thread is not None and monitor_thread is not threading.current_thread():
                    monitor_thread.join(timeout=1)
                if process is not None and process.poll() is None:
                    process.kill()
                    process.wait(timeout=2)
                with self._playback_lock:
                    if self._playback_process is process:
                        self._playback_process = None

        if cancelled() or not self.tts or not self.tts.is_available():
            return
        audio = self.tts.synthesize(text, language="he")
        if cancelled():
            return
        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "speech.mp3"
            audio_path.write_bytes(audio)
            process = subprocess.Popen(
                [player, "-nodisp", "-autoexit", "-loglevel", "error", str(audio_path)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                **hidden_subprocess_kwargs(),
            )
            with self._playback_lock:
                self._playback_process = process
            monitor_stop = threading.Event()
            monitor_thread = self._start_barge_in_monitor(playback_cancel, monitor_stop)
            try:
                while process.poll() is None:
                    if cancelled():
                        process.kill()
                        break
                    time.sleep(0.02)
                process.wait(timeout=2)
            finally:
                monitor_stop.set()
                if monitor_thread is not None and monitor_thread is not threading.current_thread():
                    monitor_thread.join(timeout=1)
                if process.poll() is None:
                    process.kill()
                with self._playback_lock:
                    if self._playback_process is process:
                        self._playback_process = None

    def speak_chunks(self, chunks, *, cancel_event=None) -> None:
        buffer = []
        size = 0
        for chunk in chunks:
            if cancel_event is not None and cancel_event.is_set():
                self.cancel_playback()
                return
            if not chunk:
                continue
            buffer.append(chunk)
            size += len(chunk)
            text = "".join(buffer)
            if size >= 80 and (text.rstrip().endswith((".", "!", "?", ":", ";", "\n")) or size >= 180):
                self.speak(text.strip(), cancel_event=cancel_event)
                buffer.clear(); size = 0
        if buffer and not (cancel_event is not None and cancel_event.is_set()):
            self.speak("".join(buffer).strip(), cancel_event=cancel_event)
