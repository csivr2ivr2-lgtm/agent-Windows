import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent_windows.final_checks import build_final_report


class Provider:
    def __init__(self, name, available=True):
        self.name = name
        self._available = available

    def is_available(self):
        return self._available


class FinalChecksTests(unittest.TestCase):
    def runtime(self):
        return SimpleNamespace(
            settings=SimpleNamespace(data_dir=__import__("pathlib").Path("data")),
            provider_manager=SimpleNamespace(providers=[Provider("local", True)]),
        )

    @patch("agent_windows.final_checks.integrations_report", return_value=[
        {"component": "Prime Agent", "status": "ACTIVE"},
        {"component": "LiveKit Agents", "status": "CODE_READY"},
    ])
    @patch("agent_windows.final_checks.realtime_check_report", return_value={
        "streaming_stt": True,
        "streaming_llm": True,
        "streaming_tts": True,
        "barge_in": True,
        "microphone_persistent": True,
    })
    @patch("agent_windows.final_checks.collect", return_value={"ffmpeg": True, "ffplay": True})
    def test_non_windows_is_honest_code_ready(self, _collect, _realtime, _integrations):
        with patch("agent_windows.final_checks.sys.platform", "linux"):
            report = build_final_report(self.runtime())
        self.assertEqual(report["overall"], "CODE_READY_EXTERNAL_VALIDATION_REQUIRED")
        self.assertFalse(report["blockers"])
        self.assertIn("Windows host validation", report["external_validation_required"])

    @patch("agent_windows.final_checks.provider_check_report", return_value=[
        {"provider": "local", "status": "FAIL"}
    ])
    @patch("agent_windows.final_checks.integrations_report", return_value=[])
    @patch("agent_windows.final_checks.realtime_check_report", return_value={
        "streaming_stt": True,
        "streaming_llm": True,
        "streaming_tts": True,
        "barge_in": True,
        "microphone_persistent": True,
    })
    @patch("agent_windows.final_checks.collect", return_value={"ffmpeg": True, "ffplay": True})
    def test_live_provider_failure_is_blocker(self, _collect, _realtime, _integrations, _providers):
        with patch("agent_windows.final_checks.sys.platform", "linux"):
            report = build_final_report(self.runtime(), live=True)
        self.assertEqual(report["overall"], "FAIL")
        self.assertTrue(any(item.startswith("provider.local") for item in report["blockers"]))


if __name__ == "__main__":
    unittest.main()
