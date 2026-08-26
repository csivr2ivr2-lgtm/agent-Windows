import unittest
from unittest.mock import patch

from agent_windows.speech import ElevenLabsTTS
from agent_windows.voice_runtime import VoiceService


class StreamingClient:
    def __init__(self):
        self.calls = []

    def iter_request(self, method, url, headers, body, timeout, *, chunk_size):
        self.calls.append((method, url, headers, body, timeout, chunk_size))
        yield b"one"
        yield b"two"

    def request(self, *args, **kwargs):
        raise AssertionError("non-streaming request should not be used")


class FakeStdin:
    def __init__(self):
        self.data = bytearray()
        self.closed = False

    def write(self, chunk):
        self.data.extend(chunk)
        return len(chunk)

    def flush(self):
        pass

    def close(self):
        self.closed = True


class FakeProcess:
    def __init__(self):
        self.stdin = FakeStdin()
        self._returncode = None
        self.killed = False

    def poll(self):
        return self._returncode

    def wait(self, timeout=None):
        self._returncode = 0
        return 0

    def kill(self):
        self.killed = True
        self._returncode = -9


class StreamingTTS:
    def __init__(self):
        self.iter_calls = []
        self.synthesize_calls = 0

    def is_available(self):
        return True

    def iter_audio(self, text, *, language=None):
        self.iter_calls.append((text, language))
        yield b"mp3-a"
        yield b"mp3-b"

    def synthesize(self, text, *, language=None):
        self.synthesize_calls += 1
        return b"should-not-be-used"


class TTSStreamingTests(unittest.TestCase):
    def test_elevenlabs_stream_endpoint_yields_audio_incrementally(self):
        client = StreamingClient()
        tts = ElevenLabsTTS("key", "voice", client=client)
        self.assertEqual(list(tts.iter_audio("שלום")), [b"one", b"two"])
        self.assertIn("/text-to-speech/voice/stream?", client.calls[0][1])
        self.assertEqual(client.calls[0][2]["xi-api-key"], "key")
        self.assertEqual(client.calls[0][5], 4096)

    def test_voice_service_prefers_direct_streaming_over_buffered_synthesis(self):
        tts = StreamingTTS()
        process = FakeProcess()
        started = []
        service = VoiceService(microphone=None, stt=None, tts=tts)

        with patch("agent_windows.voice_runtime.shutil.which", return_value="ffplay"), patch(
            "agent_windows.voice_runtime.subprocess.Popen", return_value=process
        ):
            service.speak("שלום", on_audio_start=lambda: started.append(True))

        self.assertEqual(tts.iter_calls, [("שלום", "he")])
        self.assertEqual(tts.synthesize_calls, 0)
        self.assertEqual(bytes(process.stdin.data), b"mp3-amp3-b")
        self.assertTrue(process.stdin.closed)
        self.assertEqual(started, [True])


if __name__ == "__main__":
    unittest.main()
