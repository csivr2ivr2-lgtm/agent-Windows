import threading
import time

from agent_windows.realtime import LocalRealtimeSession, RealtimeState


class SilentVAD:
    def process(self, _frame, *, timestamp_ms):
        class Result:
            speech = False
            utterance_started = False
            utterance_ended = False
        return Result()


class FakeMicStream:
    def __init__(self, keep, frames=4):
        self.keep = keep
        self.frames = frames
        self.reads = 0
        self.closed = False

    def read_frame(self):
        self.reads += 1
        if self.reads > self.frames:
            self.keep[0] = False
            return b"\0\0" * 800
        return b"\0\0" * 800

    def close(self):
        self.closed = True


class FakeMic:
    def __init__(self, keep):
        self.keep = keep
        self.opens = 0
        self.stream = None

    def open_pcm_stream(self, *, frame_ms):
        self.opens += 1
        self.stream = FakeMicStream(self.keep)
        return self.stream


class FakeSTTSession:
    provider = "fake"

    def __init__(self):
        self.sent = 0
        self.closed = False

    def send_audio(self, _frame):
        self.sent += 1

    def recv_event(self, timeout=None):
        time.sleep(0.001)
        raise TimeoutError

    def force_endpoint(self):
        pass

    def close(self):
        self.closed = True


class FakeSTTManager:
    def __init__(self):
        self.opens = 0
        self.session = FakeSTTSession()

    def open(self, *, language, sample_rate):
        self.opens += 1
        assert language == "he"
        assert sample_rate == 16000
        return self.session


class FakeVoice:
    def __init__(self, microphone):
        self.microphone = microphone
        self.playback_cancelled = 0
        self.started = threading.Event()

    def cancel_playback(self):
        self.playback_cancelled += 1

    def speak_chunks(self, chunks, *, cancel_event=None, on_audio_start=None):
        for _chunk in chunks:
            if on_audio_start:
                on_audio_start()
            self.started.set()
            while not cancel_event.is_set():
                time.sleep(0.001)
            return


class FakeRuntime:
    def __init__(self, keep):
        mic = FakeMic(keep)
        self.streaming_stt = FakeSTTManager()
        self.voice = FakeVoice(mic)

    def stream_text(self, _text, *, cancel_event=None):
        yield "x" * 100


def test_local_realtime_keeps_one_microphone_and_one_stt_session():
    keep = [True]
    runtime = FakeRuntime(keep)
    session = LocalRealtimeSession(
        runtime,
        vad_factory=SilentVAD,
        barge_vad_factory=SilentVAD,
    )
    session.run(lambda: keep[0])
    assert runtime.voice.microphone.opens == 1
    assert runtime.streaming_stt.opens == 1
    assert runtime.streaming_stt.session.sent >= 4
    assert runtime.voice.microphone.stream.closed is True
    assert runtime.streaming_stt.session.closed is True


def test_barge_in_cancels_generation_and_playback():
    keep = [True]
    runtime = FakeRuntime(keep)
    states = []
    session = LocalRealtimeSession(runtime, status_callback=states.append)
    session._start_response("שלום")
    assert runtime.voice.started.wait(1)
    session._cancel_response_for_barge_in()
    deadline = time.monotonic() + 1
    while session._response_active() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert runtime.voice.playback_cancelled == 1
    assert session.metrics.barge_in_detected is not None
    assert session.metrics.playback_stopped is not None
    assert session.metrics.milliseconds()["barge_in_to_playback_stopped"] >= 0
    assert RealtimeState.INTERRUPTING in states
    assert RealtimeState.USER_SPEAKING in states
