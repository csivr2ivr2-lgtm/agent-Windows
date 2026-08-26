from __future__ import annotations

from agent_windows.integrations import OptionalBackend, integration_matrix
from agent_windows.realtime import CancellationScope, LatencyMetrics
from agent_windows.windows_tools import build_windows_tools


def test_current_datetime_tool_exists(tmp_path):
    tools = {tool.name: tool for tool in build_windows_tools((tmp_path,))}
    value = tools["current_datetime"].invoke({})
    assert "T" in value
    assert tools["current_time"].invoke({})


def test_optional_integrations_are_non_blocking():
    rows = integration_matrix()
    assert len(rows) == 15
    assert any(row.default_enabled is True for row in rows)
    assert all(row.upstream_url for row in rows)
    assert OptionalBackend("definitely_missing_agent_windows_component").healthy is False


def test_cancellation_scope():
    scope = CancellationScope()
    assert scope.cancelled is False
    scope.cancel()
    assert scope.cancelled is True


def test_latency_metrics_reports_pairs(monkeypatch):
    values = iter([1.0, 1.1, 1.2, 1.3])
    monkeypatch.setattr("agent_windows.realtime.time.monotonic", lambda: next(values))
    metrics = LatencyMetrics()
    metrics.mark("speech_end")
    metrics.mark("transcript_ready")
    metrics.mark("first_llm_token")
    metrics.mark("first_audio_byte")
    result = metrics.milliseconds()
    assert result["speech_end_to_transcript_ready"] == 100.0
    assert result["transcript_ready_to_first_llm_token"] == 100.0


def test_assemblyai_streaming_sends_pcm_and_returns_final():
    from agent_windows.speech import AssemblyAISTT

    class FakeWS:
        def __init__(self):
            self.sent_binary = []
            self.sent_text = []
            self.messages = iter([
                '{"type":"Turn","transcript":"שלום","end_of_turn":false}',
                '{"type":"Turn","transcript":"שלום עולם","end_of_turn":true}',
                '{"type":"Termination"}',
            ])
        def send_binary(self, payload): self.sent_binary.append(payload)
        def send(self, payload): self.sent_text.append(payload)
        def recv(self): return next(self.messages, "")
        def close(self): pass

    ws = FakeWS()
    provider = AssemblyAISTT("key", ws_factory=lambda *_: ws)
    result = provider.transcribe_stream([b"a" * 1600, b"b" * 1600])
    assert result == "שלום עולם"
    assert ws.sent_binary == [b"a" * 1600, b"b" * 1600]
    assert any("ForceEndpoint" in item for item in ws.sent_text)
    assert any("Terminate" in item for item in ws.sent_text)


def test_streaming_stt_manager_replays_primary_audio_to_fallback():
    from agent_windows.errors import ProviderConnectionError
    from agent_windows.speech import STTManager

    class Primary:
        name = "primary"
        def is_available(self): return True
        def transcribe_stream(self, frames, **_kwargs):
            iterator = iter(frames)
            assert next(iterator) == b"one"
            raise ProviderConnectionError("drop")

    class Fallback:
        name = "fallback"
        def is_available(self): return True
        def transcribe_stream(self, frames, **_kwargs):
            assert list(frames) == [b"one", b"two", b"three"]
            return "recovered"

    manager = STTManager([Primary(), Fallback()])
    assert manager.transcribe_stream(iter([b"one", b"two", b"three"])) == "recovered"


def test_barge_in_monitor_cancels_playback():
    import threading
    from agent_windows.voice_runtime import VoiceService

    class Mic:
        def wait_for_speech(self, _stop_event): return True

    voice = VoiceService(microphone=Mic(), stt=None, tts=None)
    cancelled = threading.Event()
    voice.cancel_playback = cancelled.set
    playback_cancel = threading.Event()
    monitor_stop = threading.Event()
    thread = voice._start_barge_in_monitor(playback_cancel, monitor_stop)
    assert thread is not None
    thread.join(timeout=1)
    assert playback_cancel.is_set()
    assert cancelled.is_set()


def test_livekit_adapter_is_active_runtime_not_import_probe():
    from agent_windows.realtime import LiveKitSessionAdapter

    adapter = LiveKitSessionAdapter(enabled=False)
    assert adapter.available() is False
    assert callable(adapter.build_server)
    assert callable(adapter.run)
