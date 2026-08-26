import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from agent_windows.errors import ProviderConnectionError
from agent_windows.realtime import LocalRealtimeSession, RealtimeState


class SilentVAD:
    def process(self, _frame, *, timestamp_ms):
        return SimpleNamespace(speech=False, utterance_started=False, utterance_ended=False)


class SequenceVAD:
    def __init__(self, results):
        self.results = iter(results)
    def process(self, _frame, *, timestamp_ms):
        return next(self.results, SimpleNamespace(speech=False, utterance_started=False, utterance_ended=False))


class FakeMicStream:
    def __init__(self, keep, frames=4, empty=False):
        self.keep = keep
        self.frames = frames
        self.reads = 0
        self.closed = False
        self.empty = empty
    def read_frame(self):
        self.reads += 1
        if self.empty:
            return b""
        if self.reads > self.frames:
            self.keep[0] = False
        return b"\0\0" * 800
    def close(self): self.closed = True


class FakeMic:
    def __init__(self, keep, *, empty=False):
        self.keep = keep; self.opens = 0; self.stream = None; self.empty = empty
    def open_pcm_stream(self, *, frame_ms):
        self.opens += 1; self.stream = FakeMicStream(self.keep, empty=self.empty); return self.stream


class FakeSTTSession:
    provider = "fake"
    def __init__(self, events=()):
        self.sent = 0; self.closed = False; self.forced = 0; self.events = iter(events)
    def send_audio(self, _frame): self.sent += 1
    def recv_event(self, timeout=None):
        try:
            value = next(self.events)
        except StopIteration:
            time.sleep(0.001); raise TimeoutError
        if isinstance(value, Exception): raise value
        return value
    def force_endpoint(self): self.forced += 1
    def close(self): self.closed = True


class FakeSTTManager:
    def __init__(self, session=None): self.opens = 0; self.session = session or FakeSTTSession()
    def open(self, *, language, sample_rate):
        self.opens += 1
        assert language == "he" and sample_rate == 16000
        return self.session


class FakeVoice:
    def __init__(self, microphone):
        self.microphone = microphone; self.playback_cancelled = 0; self.started = threading.Event(); self.fail = False
    def cancel_playback(self): self.playback_cancelled += 1
    def speak_chunks(self, chunks, *, cancel_event=None, on_audio_start=None):
        if self.fail: raise RuntimeError("speaker failed")
        for _chunk in chunks:
            if on_audio_start: on_audio_start()
            self.started.set()
            while cancel_event is not None and not cancel_event.is_set():
                time.sleep(0.001)
            return


class FakeRuntime:
    def __init__(self, keep, *, stt_session=None, empty_mic=False):
        mic = FakeMic(keep, empty=empty_mic)
        self.streaming_stt = FakeSTTManager(stt_session)
        self.voice = FakeVoice(mic)
        self.stream_calls = []
    def stream_text(self, text, *, cancel_event=None, history=None):
        self.stream_calls.append((text, tuple(history or ())))
        yield "x" * 100


class FakeResponseThread:
    def __init__(self): self.joined = False
    def is_alive(self): return True
    def join(self, timeout=None): self.joined = True


class LocalRealtimeTests(unittest.TestCase):
    def test_local_realtime_keeps_one_microphone_and_one_stt_session(self):
        keep = [True]; runtime = FakeRuntime(keep)
        session = LocalRealtimeSession(runtime, vad_factory=SilentVAD, barge_vad_factory=SilentVAD)
        session.run(lambda: keep[0])
        self.assertEqual(runtime.voice.microphone.opens, 1)
        self.assertEqual(runtime.streaming_stt.opens, 1)
        self.assertGreaterEqual(runtime.streaming_stt.session.sent, 4)
        self.assertTrue(runtime.voice.microphone.stream.closed)
        self.assertTrue(runtime.streaming_stt.session.closed)

    def test_barge_in_cancels_generation_and_playback(self):
        keep = [True]; runtime = FakeRuntime(keep); states = []
        session = LocalRealtimeSession(runtime, status_callback=states.append)
        session._start_response("שלום")
        self.assertTrue(runtime.voice.started.wait(1))
        session._cancel_response_for_barge_in()
        deadline = time.monotonic() + 1
        while session._response_active() and time.monotonic() < deadline: time.sleep(0.005)
        self.assertEqual(runtime.voice.playback_cancelled, 1)
        self.assertIsNotNone(session.metrics.barge_in_detected)
        self.assertGreaterEqual(session.metrics.milliseconds()["barge_in_to_playback_stopped"], 0)
        self.assertIn(RealtimeState.INTERRUPTING, states)
        self.assertIn(RealtimeState.USER_SPEAKING, states)
        session._cancel_response_for_barge_in()

    def test_barge_in_next_turn_receives_interrupted_session_history(self):
        keep = [True]
        runtime = FakeRuntime(keep)
        session = LocalRealtimeSession(runtime)
        session._start_response("תסביר לי מה זה MCP")
        self.assertTrue(runtime.voice.started.wait(1))
        session._cancel_response_for_barge_in()

        session._start_response("רק במשפט אחד")
        deadline = time.monotonic() + 1
        while len(runtime.stream_calls) < 2 and time.monotonic() < deadline:
            time.sleep(0.005)

        self.assertGreaterEqual(len(runtime.stream_calls), 2)
        _, history = runtime.stream_calls[1]
        self.assertEqual(history[0].role, "user")
        self.assertEqual(history[0].content, "תסביר לי מה זה MCP")
        self.assertEqual(history[1].role, "assistant")
        self.assertIn("התגובה נקטעה", history[1].content)

        session._cancel_response_for_barge_in()

    def test_response_blank_error_and_metrics(self):
        keep = [True]; runtime = FakeRuntime(keep); states=[]
        session = LocalRealtimeSession(runtime, status_callback=states.append)
        session._start_response("   ")
        self.assertFalse(session._response_active())
        runtime.voice.fail = True
        session._start_response("x")
        deadline = time.monotonic() + 1
        while session._response_active() and time.monotonic() < deadline: time.sleep(0.005)
        self.assertIn(RealtimeState.ERROR, states)
        session.metrics.speech_end = 1.0; session.metrics.transcript_ready = 1.1
        session.metrics.first_llm_token = 1.2; session.metrics.first_audio_byte = 1.3; session.metrics.first_audible = 1.4
        self.assertEqual(session.metrics.milliseconds()["speech_end_to_transcript_ready"], 100.0)
        with mock.patch("agent_windows.realtime.logger.info") as info:
            session.metrics.log(); info.assert_called_once()

    def test_receiver_filters_events_and_handles_failure(self):
        keep_count = [20]
        def keep(): keep_count[0] -= 1; return keep_count[0] > 0
        events = [
            None,
            SimpleNamespace(speech_started=True, is_final=False, text=""),
            SimpleNamespace(speech_started=False, is_final=True, text="   "),
            SimpleNamespace(speech_started=False, is_final=True, text="hello"),
        ]
        stt = FakeSTTSession(events)
        runtime = FakeRuntime([True], stt_session=stt)
        session = LocalRealtimeSession(runtime)
        with mock.patch.object(session, "_start_response") as start:
            session._receiver(stt, keep)
            start.assert_called_once_with("hello")
        failing = FakeSTTSession([ProviderConnectionError("down")])
        session._session_error = None
        session._receiver(failing, lambda: True)
        self.assertIsInstance(session._session_error, ProviderConnectionError)

    def test_receiver_ignores_final_during_unaccepted_playback(self):
        event = SimpleNamespace(speech_started=False, is_final=True, text="interrupt")
        stt = FakeSTTSession([event]); session = LocalRealtimeSession(FakeRuntime([True], stt_session=stt))
        session._response_thread = FakeResponseThread(); session._barge_accepted = False
        with mock.patch.object(session, "_start_response") as start:
            calls=[True, False]
            session._receiver(stt, lambda: calls.pop(0) if calls else False)
            start.assert_not_called()

    def test_run_normal_vad_endpoint_and_empty_microphone_failure(self):
        keep=[True]; runtime=FakeRuntime(keep)
        normal = SequenceVAD([
            SimpleNamespace(speech=False, utterance_started=True, utterance_ended=False),
            SimpleNamespace(speech=False, utterance_started=False, utterance_ended=True),
        ])
        session = LocalRealtimeSession(runtime, vad_factory=lambda: normal, barge_vad_factory=SilentVAD)
        session.run(lambda: keep[0])
        self.assertGreaterEqual(runtime.streaming_stt.session.forced, 1)

        keep=[True]; runtime=FakeRuntime(keep, empty_mic=True)
        session=LocalRealtimeSession(runtime, vad_factory=SilentVAD, barge_vad_factory=SilentVAD)
        with self.assertRaises(ProviderConnectionError): session.run(lambda: True)
        self.assertTrue(runtime.voice.microphone.stream.closed)

    def test_run_barge_vad_branch_and_session_error(self):
        keep=[True]; runtime=FakeRuntime(keep)
        barge = SequenceVAD([SimpleNamespace(speech=True, utterance_started=False, utterance_ended=True)])
        session=LocalRealtimeSession(runtime, barge_in_frames=1, vad_factory=SilentVAD, barge_vad_factory=lambda: barge)
        fake_thread=FakeResponseThread(); session._response_thread=fake_thread
        count=[0]
        def running():
            count[0]+=1
            if count[0] > 1: keep[0]=False
            return keep[0]
        session.run(running)
        self.assertGreaterEqual(runtime.voice.playback_cancelled, 1)
        self.assertTrue(fake_thread.joined)

        keep=[True]; runtime=FakeRuntime(keep); session=LocalRealtimeSession(runtime)
        session._session_error=ProviderConnectionError("receiver down")
        with self.assertRaises(ProviderConnectionError): session.run(lambda: True)


if __name__ == "__main__":
    unittest.main()
