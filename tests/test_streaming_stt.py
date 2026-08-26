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


class SessionProvider:
    def __init__(self, name, sessions):
        self.name = name
        self.sessions = list(sessions)
        self.opens = 0

    def is_available(self):
        return True

    def open(self, *, language, sample_rate):
        self.opens += 1
        value = self.sessions.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class RecoverableSession:
    def __init__(self, provider, *, recv_error=None, send_error=None, event=None):
        self.provider = provider
        self.recv_error = recv_error
        self.send_error = send_error
        self.event = event
        self.closed = False
        self.sent = []

    def send_audio(self, frame):
        if self.send_error is not None:
            error = self.send_error
            self.send_error = None
            raise error
        self.sent.append(frame)

    def recv_event(self, timeout=None):
        if self.recv_error is not None:
            error = self.recv_error
            self.recv_error = None
            raise error
        return self.event

    def force_endpoint(self):
        pass

    def close(self):
        self.closed = True


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

    def test_runtime_receive_failure_falls_back_sequentially(self):
        first_session = RecoverableSession(
            "assemblyai", recv_error=ProviderConnectionError("socket closed")
        )
        second_session = RecoverableSession("deepgram")
        first = SessionProvider("assemblyai", [first_session])
        second = SessionProvider("deepgram", [second_session])
        session = StreamingSTTManager(
            [first, second], max_reconnects_per_provider=0
        ).open()

        self.assertEqual(session.provider, "assemblyai")
        self.assertIsNone(session.recv_event(timeout=0.1))
        self.assertTrue(first_session.closed)
        self.assertEqual(session.provider, "deepgram")
        self.assertEqual(first.opens, 1)
        self.assertEqual(second.opens, 1)

    def test_transient_failure_reconnects_same_provider_once(self):
        broken = RecoverableSession(
            "assemblyai", recv_error=ProviderConnectionError("temporary")
        )
        recovered = RecoverableSession("assemblyai")
        provider = SessionProvider("assemblyai", [broken, recovered])
        session = StreamingSTTManager(
            [provider], max_reconnects_per_provider=1
        ).open()

        self.assertIsNone(session.recv_event(timeout=0.1))
        self.assertTrue(broken.closed)
        self.assertEqual(session.provider, "assemblyai")
        self.assertEqual(provider.opens, 2)

    def test_send_failure_retries_current_frame_after_fallback(self):
        broken = RecoverableSession(
            "assemblyai", send_error=ProviderConnectionError("send failed")
        )
        fallback = RecoverableSession("deepgram")
        session = StreamingSTTManager(
            [
                SessionProvider("assemblyai", [broken]),
                SessionProvider("deepgram", [fallback]),
            ],
            max_reconnects_per_provider=0,
        ).open()

        session.send_audio(b"frame")
        self.assertTrue(broken.closed)
        self.assertEqual(session.provider, "deepgram")
        self.assertEqual(fallback.sent, [b"frame"])


if __name__ == "__main__":
    unittest.main()
