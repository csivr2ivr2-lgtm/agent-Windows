import json
import unittest

from agent_windows.errors import ProviderConnectionError
from agent_windows.streaming_stt import (
    AssemblyAIStreamingSTT,
    DeepgramStreamingSTT,
    StreamingSTTManager,
)


class FakeWS:
    def __init__(self, incoming=()):
        self.incoming = iter(incoming)
        self.sent = []
        self.closed = False

    def send(self, message):
        self.sent.append(message)

    def recv(self, timeout=None):
        return next(self.incoming)

    def close(self):
        self.closed = True


class Factory:
    def __init__(self, ws=None, error=None):
        self.ws = ws or FakeWS()
        self.error = error
        self.calls = []

    def __call__(self, url, headers, timeout):
        self.calls.append((url, headers, timeout))
        if self.error:
            raise self.error
        return self.ws


class StreamingSTTTests(unittest.TestCase):
    def test_assemblyai_hebrew_uses_whisper_streaming_and_raw_auth_header(self):
        ws = FakeWS([
            json.dumps({"type": "SpeechStarted"}),
            json.dumps({"type": "Turn", "transcript": "של", "end_of_turn": False}),
            json.dumps({"type": "Turn", "transcript": "שלום", "end_of_turn": True}),
        ])
        factory = Factory(ws)
        session = AssemblyAIStreamingSTT("key", websocket_factory=factory).open(language="he")
        url, headers, _ = factory.calls[0]
        self.assertIn("speech_model=whisper-rt", url)
        self.assertIn("sample_rate=16000", url)
        self.assertEqual(headers["Authorization"], "key")
        session.send_audio(b"\0\1" * 800)
        self.assertTrue(session.recv_event().speech_started)
        self.assertFalse(session.recv_event().is_final)
        final = session.recv_event()
        self.assertTrue(final.is_final)
        self.assertEqual(final.text, "שלום")
        session.close()
        self.assertIn('{"type": "Terminate"}', ws.sent)
        self.assertTrue(ws.closed)

    def test_deepgram_streaming_partial_final_and_finalize(self):
        ws = FakeWS([
            json.dumps({
                "type": "Results",
                "channel": {"alternatives": [{"transcript": "של"}]},
                "is_final": False,
                "speech_final": False,
            }),
            json.dumps({
                "type": "Results",
                "channel": {"alternatives": [{"transcript": "שלום"}]},
                "is_final": True,
                "speech_final": True,
            }),
        ])
        factory = Factory(ws)
        session = DeepgramStreamingSTT("dg", websocket_factory=factory).open(language="he")
        url, headers, _ = factory.calls[0]
        self.assertIn("model=nova-3", url)
        self.assertIn("language=he", url)
        self.assertIn("interim_results=true", url)
        self.assertEqual(headers["Authorization"], "Token dg")
        self.assertFalse(session.recv_event().is_final)
        self.assertTrue(session.recv_event().is_final)
        session.force_endpoint()
        self.assertIn('{"type": "Finalize"}', ws.sent)

    def test_manager_falls_back_without_opening_providers_in_parallel(self):
        first = Factory(error=ProviderConnectionError("down"))
        second = Factory(FakeWS())
        providers = [
            AssemblyAIStreamingSTT("a", websocket_factory=first),
            DeepgramStreamingSTT("d", websocket_factory=second),
        ]
        session = StreamingSTTManager(providers).open(language="he")
        self.assertEqual(session.provider, "deepgram")
        self.assertEqual(len(first.calls), 1)
        self.assertEqual(len(second.calls), 1)

    def test_manager_reports_no_configured_provider(self):
        manager = StreamingSTTManager([
            AssemblyAIStreamingSTT(""),
            DeepgramStreamingSTT(""),
        ])
        with self.assertRaises(ProviderConnectionError):
            manager.open()


if __name__ == "__main__":
    unittest.main()
