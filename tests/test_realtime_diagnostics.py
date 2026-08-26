import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent_windows.config import Settings
from agent_windows.diagnostics import realtime_check_report


class Provider:
    def __init__(self, available): self.available = available
    def is_available(self): return self.available


class RealtimeDiagnosticsTests(unittest.TestCase):
    def test_report_distinguishes_configuration_from_features(self):
        settings = Settings(
            data_dir=Path("data"), log_level="ERROR", llm_order=(), llm_timeout=30,
            llm_attempts=1, retry_base=.1, retry_max=1, transient_cooldown=1, rate_cooldown=1,
            auth_cooldown=1, groq_key="", groq_model="", gemini_key="", gemini_model="",
            openrouter_key="", openrouter_model="", local_llm_url="", local_llm_model="",
            assemblyai_key="x", deepgram_key="", stt_order=("assemblyai",), elevenlabs_key="x",
            elevenlabs_voice="voice", elevenlabs_model="eleven_v3", relay_url="", relay_token="",
            direct_allowed=True, microphone_device="default", livekit_url="wss://x",
            livekit_api_key="key", livekit_api_secret="secret", realtime_backend="auto",
            livekit_agent_name="ai-aharon",
        )
        runtime = SimpleNamespace(
            settings=settings,
            streaming_stt=SimpleNamespace(providers=[Provider(True)]),
            tts=SimpleNamespace(is_available=lambda: True, iter_audio=lambda text: iter(())),
            relay=None,
            provider_manager=SimpleNamespace(stream=lambda *args: iter(())),
            voice=SimpleNamespace(
                cancel_playback=lambda: None,
                microphone=SimpleNamespace(open_pcm_stream=lambda: None),
            ),
        )
        with patch("agent_windows.livekit_runtime.livekit_installed", return_value=True):
            report = realtime_check_report(runtime)
        self.assertEqual(report["backend"], "livekit")
        self.assertTrue(report["livekit_configured"])
        self.assertTrue(report["streaming_stt"])
        self.assertTrue(report["streaming_llm"])
        self.assertTrue(report["streaming_tts"])
        self.assertTrue(report["barge_in"])
        self.assertTrue(report["microphone_persistent"])


if __name__ == "__main__": unittest.main()
