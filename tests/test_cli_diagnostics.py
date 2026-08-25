import io
import json
import subprocess
import unittest
from unittest.mock import Mock, patch

from agent_windows.__main__ import main
from agent_windows.diagnostics import run_llmfit, total_memory_bytes


class DummyVoice:
    def listen(self):
        return "hello"

    def speak(self, text):
        self.spoken = text


class DummyRuntime:
    relay = None

    def __init__(self, _settings):
        self.voice = DummyVoice()

    def handle_text(self, text):
        return f"answer:{text}"


class CLIDiagnosticsTests(unittest.TestCase):
    def test_status_outputs_json(self):
        with patch("agent_windows.__main__.AgentRuntime", DummyRuntime), \
             patch("agent_windows.__main__.collect", return_value={"status": "ok"}), \
             patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(main(["--env", "missing.env", "status"]), 0)
            self.assertEqual(json.loads(output.getvalue()), {"status": "ok"})

    def test_doctor_runs_llmfit_only_when_requested(self):
        with patch("agent_windows.__main__.AgentRuntime", DummyRuntime), \
             patch("agent_windows.__main__.collect", return_value={}), \
             patch("agent_windows.__main__.run_llmfit", return_value="recommendation") as llmfit, \
             patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(main(["doctor", "--llmfit"]), 0)
            llmfit.assert_called_once_with()
            self.assertIn("recommendation", output.getvalue())

    def test_chat_and_voice_paths(self):
        with patch("agent_windows.__main__.AgentRuntime", DummyRuntime), \
             patch("builtins.input", side_effect=["hello", "exit"]), \
             patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(main(["chat"]), 0)
            self.assertIn("answer:hello", output.getvalue())
        with patch("agent_windows.__main__.AgentRuntime", DummyRuntime), \
             patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(main(["voice"]), 0)
            self.assertIn("Agent: answer:hello", output.getvalue())

    def test_voice_failure_is_safe(self):
        runtime = DummyRuntime(None)
        runtime.voice.listen = Mock(side_effect=RuntimeError("device unavailable"))
        with patch("agent_windows.__main__.AgentRuntime", return_value=runtime), \
             patch("sys.stderr", new_callable=io.StringIO) as error:
            self.assertEqual(main(["voice"]), 2)
            self.assertIn("Voice unavailable", error.getvalue())

    def test_llmfit_failure_modes(self):
        with patch("agent_windows.diagnostics.shutil.which", return_value=None):
            self.assertIn("not installed", run_llmfit())
        with patch("agent_windows.diagnostics.shutil.which", return_value="llmfit"), \
             patch("agent_windows.diagnostics.subprocess.run", side_effect=subprocess.TimeoutExpired("llmfit", 30)):
            self.assertIn("timed out", run_llmfit())
        with patch("agent_windows.diagnostics.shutil.which", return_value="llmfit"), \
             patch("agent_windows.diagnostics.subprocess.run", side_effect=OSError("blocked")):
            self.assertIn("could not start", run_llmfit())

    def test_memory_detection_handles_platform_errors(self):
        with patch("agent_windows.diagnostics.sys.platform", "linux"), \
             patch("agent_windows.diagnostics.os.sysconf", side_effect=OSError("unsupported")):
            self.assertIsNone(total_memory_bytes())


if __name__ == "__main__":
    unittest.main()
