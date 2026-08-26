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
