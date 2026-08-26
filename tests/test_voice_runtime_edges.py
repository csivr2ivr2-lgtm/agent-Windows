import io
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from agent_windows.audio import NetworkState
from agent_windows.errors import ProviderConnectionError, ProviderServerError
from agent_windows.voice_runtime import (
    FFmpegMicrophone,
    FFmpegPCMStream,
    MicrophoneUnavailable,
    VoiceService,
    _audio_metadata,
    _spool_file,
)


class Process:
    def __init__(self, stdout=b"", *, wait_timeout_once=False, stdin=True):
        self.stdout = io.BytesIO(stdout) if stdout is not None else None
        self.stdin = FakeStdin() if stdin else None
        self.returncode = None
        self.terminated = 0
        self.killed = 0
        self.wait_timeout_once = wait_timeout_once
        self.waits = 0

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated += 1

    def kill(self):
        self.killed += 1
        self.returncode = -9

    def wait(self, timeout=None):
        self.waits += 1
        if self.wait_timeout_once and self.waits == 1:
            raise subprocess.TimeoutExpired("fake", timeout)
        self.returncode = 0
        return 0


class FakeStdin:
    def __init__(self):
        self.data = bytearray()
        self.closed = False

    def write(self, data):
        self.data.extend(data)
        return len(data)

    def flush(self):
        pass

    def close(self):
        self.closed = True


class VoiceRuntimeEdges(unittest.TestCase):
    def test_audio_metadata_and_spool_file(self):
        profile = SimpleNamespace(
            codec="pcm_s16le", content_type="audio/L16", sample_rate=16000,
            channels=1, bitrate_bps=256000, chunk_ms=50,
        )
        self.assertEqual(_audio_metadata(profile)["codec"], "pcm_s16le")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "a.raw"
            path.write_bytes(b"x" * 70000)
            spool = SimpleNamespace(put=mock.Mock())
            _spool_file(spool, path, "s" * 16, profile)
            self.assertGreaterEqual(spool.put.call_count, 2)
            metadata = spool.put.call_args.kwargs["session_metadata"]
            self.assertEqual(metadata["sample_rate"], 16000)

    def test_pcm_stream_read_close_and_timeout_kill(self):
        process = Process(b"abcdef")
        stream = FFmpegPCMStream(process, frame_bytes=3)
        self.assertEqual(stream.read_frame(), b"abc")
        stream.close()
        self.assertTrue(stream.closed)
        self.assertEqual(process.terminated, 1)
        self.assertEqual(stream.read_frame(), b"")
        stream.close()

        process2 = Process(b"", wait_timeout_once=True)
        with FFmpegPCMStream(process2, frame_bytes=2) as stream2:
            pass
        self.assertEqual(process2.killed, 1)

        no_stdout = Process(None)
        no_stdout.stdout = None
        self.assertEqual(FFmpegPCMStream(no_stdout, frame_bytes=2).read_frame(), b"")

    def test_open_pcm_stream_validation_and_success(self):
        mic = FFmpegMicrophone("Mic")
        with self.assertRaises(ValueError):
            mic.open_pcm_stream(frame_ms=10)
        with mock.patch("agent_windows.voice_runtime.shutil.which", return_value=None):
            with self.assertRaises(MicrophoneUnavailable):
                mic.open_pcm_stream()
        with (
            mock.patch("agent_windows.voice_runtime.shutil.which", return_value="ffmpeg"),
            mock.patch("sys.platform", "linux"),
        ):
            with self.assertRaises(MicrophoneUnavailable):
                mic.open_pcm_stream()

        process = Process(b"x" * 4000)
        with (
            mock.patch("agent_windows.voice_runtime.shutil.which", return_value="ffmpeg"),
            mock.patch("sys.platform", "win32"),
            mock.patch("agent_windows.voice_runtime.subprocess.Popen", return_value=process) as popen,
        ):
            stream = mic.open_pcm_stream(frame_ms=50)
        self.assertEqual(stream.frame_bytes, 1600)
        command = popen.call_args.args[0]
        self.assertIn("audio=Mic", command)
        stream.close()

    def test_capture_pcm_utterance_success_and_no_audio(self):
        class VAD:
            def __init__(self):
                self.calls = 0

            def process(self, frame, *, timestamp_ms):
                self.calls += 1
                return SimpleNamespace(
                    utterance_started=self.calls == 1,
                    utterance_ended=self.calls == 2,
                )

        process = Process((b"a" * 640) + (b"b" * 640))
        mic = FFmpegMicrophone("Mic", max_seconds=5)
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch("agent_windows.voice_runtime.shutil.which", return_value="ffmpeg"), \
             mock.patch("sys.platform", "win32"), \
             mock.patch("agent_windows.voice_runtime.subprocess.Popen", return_value=process):
            target = Path(directory) / "u.pcm"
            mic.capture_pcm_utterance(target, VAD())
            self.assertEqual(target.stat().st_size, 1280)
        self.assertEqual(process.terminated, 1)

        empty_process = Process(b"")
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch("agent_windows.voice_runtime.shutil.which", return_value="ffmpeg"), \
             mock.patch("sys.platform", "win32"), \
             mock.patch("agent_windows.voice_runtime.subprocess.Popen", return_value=empty_process):
            with self.assertRaises(MicrophoneUnavailable):
                mic.capture_pcm_utterance(Path(directory) / "empty.pcm", VAD())

    def test_capture_pcm_utterance_validates_environment(self):
        mic = FFmpegMicrophone()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "x"
            with mock.patch("agent_windows.voice_runtime.shutil.which", return_value=None):
                with self.assertRaises(MicrophoneUnavailable):
                    mic.capture_pcm_utterance(target, mock.Mock())
            with (
                mock.patch("agent_windows.voice_runtime.shutil.which", return_value="ffmpeg"),
                mock.patch("sys.platform", "linux"),
            ):
                with self.assertRaises(MicrophoneUnavailable):
                    mic.capture_pcm_utterance(target, mock.Mock())

    def _listen_service(self, state=NetworkState.GOOD, relay=None, direct=True):
        class Mic:
            def capture_pcm_utterance(self, target, vad):
                target.write_bytes(b"\0\0" * 640)

        class Provider:
            supported_codecs = {"pcm_s16le"}
            def is_available(self): return True

        class STT:
            providers = [Provider()]
            def transcribe(self, audio, **kwargs): return "direct"

        network = SimpleNamespace(state=state)
        spool = SimpleNamespace(put=mock.Mock())
        return VoiceService(
            microphone=Mic(), stt=STT(), tts=None, relay=relay,
            network_monitor=network, spool=spool, direct_allowed=direct,
        ), spool

    @staticmethod
    def _encode_ok(*args, **kwargs):
        kwargs["stdout"].write(b"encoded-audio")
        return SimpleNamespace(returncode=0)

    def test_listen_encoding_failure_and_offline_spool(self):
        service, spool = self._listen_service(state=NetworkState.OFFLINE)
        with (
            mock.patch("agent_windows.voice_runtime.FFmpegCapabilities.supported_codecs", return_value={"pcm_s16le"}),
            mock.patch("agent_windows.voice_runtime.subprocess.run", side_effect=self._encode_ok),
        ):
            with self.assertRaisesRegex(ProviderConnectionError, "offline"):
                service.listen()
        self.assertTrue(spool.put.called)

        service, _ = self._listen_service()
        with (
            mock.patch("agent_windows.voice_runtime.FFmpegCapabilities.supported_codecs", return_value={"pcm_s16le"}),
            mock.patch("agent_windows.voice_runtime.subprocess.run", return_value=SimpleNamespace(returncode=1)),
        ):
            with self.assertRaisesRegex(RuntimeError, "encoding"):
                service.listen()

    def test_listen_relay_transcript_and_relay_failure_spools_then_direct(self):
        relay = SimpleNamespace(
            is_available=lambda: True,
            health=lambda: True,
        )
        service, _spool = self._listen_service(relay=relay)
        uploader = mock.Mock()
        uploader.upload.return_value = ({"transcript": "relay text"}, object())
        with (
            mock.patch("agent_windows.voice_runtime.FFmpegCapabilities.supported_codecs", return_value={"pcm_s16le"}),
            mock.patch("agent_windows.voice_runtime.subprocess.run", side_effect=self._encode_ok),
            mock.patch("agent_windows.voice_runtime.ResilientUploader", return_value=uploader),
        ):
            self.assertEqual(service.listen(), "relay text")

        service, spool = self._listen_service(relay=relay)
        failing = mock.Mock()
        failing.upload.side_effect = ProviderServerError("relay down")
        with (
            mock.patch("agent_windows.voice_runtime.FFmpegCapabilities.supported_codecs", return_value={"pcm_s16le"}),
            mock.patch("agent_windows.voice_runtime.subprocess.run", side_effect=self._encode_ok),
            mock.patch("agent_windows.voice_runtime.ResilientUploader", return_value=failing),
        ):
            self.assertEqual(service.listen(), "direct")
        self.assertTrue(spool.put.called)

    def test_cancel_playback_normal_and_timeout(self):
        service = VoiceService(microphone=None, stt=None, tts=None)
        process = Process()
        service._playback_process = process
        service.cancel_playback()
        self.assertEqual(process.killed, 1)

        process = Process(wait_timeout_once=True)
        service._playback_process = process
        service.cancel_playback()
        self.assertEqual(process.killed, 1)

        process.returncode = 0
        service.cancel_playback()

    def test_play_stream_empty_cancel_and_missing_stdin(self):
        service = VoiceService(microphone=None, stt=None, tts=None)
        process = Process()
        with mock.patch("agent_windows.voice_runtime.subprocess.Popen", return_value=process):
            self.assertFalse(service._play_stream("ffplay", [b"", b""]))
        self.assertIsNone(service._playback_process)

        event = threading.Event(); event.set()
        process = Process()
        with mock.patch("agent_windows.voice_runtime.subprocess.Popen", return_value=process):
            self.assertFalse(service._play_stream("ffplay", [b"audio"], cancel_event=event))
        self.assertEqual(process.killed, 1)

        process = Process(stdin=False)
        with mock.patch("agent_windows.voice_runtime.subprocess.Popen", return_value=process):
            self.assertFalse(service._play_stream("ffplay", [b"audio"]))

    def test_speak_paths_and_buffered_fallback(self):
        service = VoiceService(microphone=None, stt=None, tts=None)
        with mock.patch("agent_windows.voice_runtime.shutil.which", return_value=None):
            service.speak("x")

        event = threading.Event(); event.set()
        with mock.patch("agent_windows.voice_runtime.shutil.which", return_value="ffplay"):
            service.speak("x", cancel_event=event)

        class BufferedTTS:
            def is_available(self): return True
            def synthesize(self, text, *, language=None): return b"mp3"

        process = Process(stdin=False)
        process.returncode = 0
        service = VoiceService(microphone=None, stt=None, tts=BufferedTTS())
        started = []
        with (
            mock.patch("agent_windows.voice_runtime.shutil.which", return_value="ffplay"),
            mock.patch("agent_windows.voice_runtime.subprocess.Popen", return_value=process),
        ):
            service.speak("שלום", on_audio_start=lambda: started.append(1))
        self.assertEqual(started, [1])

        unavailable = SimpleNamespace(is_available=lambda: False)
        service = VoiceService(microphone=None, stt=None, tts=unavailable)
        with (
            mock.patch(
                "agent_windows.voice_runtime.shutil.which",
                side_effect=lambda program: "ffplay" if program == "ffplay" else None,
            ),
            mock.patch.object(service, "_speak_local_sapi") as sapi,
        ):
            service.speak("x")
        sapi.assert_called_once_with("x", cancel_event=None, on_audio_start=None)

    def test_speak_relay_failure_falls_back_and_speak_chunks(self):
        relay = SimpleNamespace(
            is_available=lambda: True,
            iter_tts=lambda *args, **kwargs: (_ for _ in ()).throw(ProviderServerError("relay")),
        )

        class TTS:
            def is_available(self): return True
            def iter_audio(self, text, *, language=None): yield b"ok"
            def synthesize(self, text, *, language=None): raise AssertionError

        service = VoiceService(microphone=None, stt=None, tts=TTS(), relay=relay)
        direct_process = Process()
        with (
            mock.patch("agent_windows.voice_runtime.shutil.which", return_value="ffplay"),
            mock.patch(
                "agent_windows.voice_runtime.subprocess.Popen",
                return_value=direct_process,
            ) as popen,
        ):
            service.speak("שלום")
        self.assertEqual(popen.call_count, 1)
        self.assertEqual(bytes(direct_process.stdin.data), b"ok")

        service = VoiceService(microphone=None, stt=None, tts=None)
        service.speak = mock.Mock()
        service.speak_chunks(["a" * 81 + ".", "tail"])
        self.assertEqual(service.speak.call_count, 2)
        event = threading.Event(); event.set()
        service.cancel_playback = mock.Mock()
        service.speak_chunks(["ignored"], cancel_event=event)
        service.cancel_playback.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
