import threading
import unittest
from unittest import mock

from agent_windows.voice_runtime import VoiceService


class _Process:
    def __init__(self):
        self.killed = False
        self._polls = 0

    def poll(self):
        self._polls += 1
        return None if self._polls == 1 and not self.killed else 0

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed = True


class LocalTtsTests(unittest.TestCase):
    def service(self):
        return VoiceService(microphone=None, stt=None, tts=None)

    @mock.patch("agent_windows.voice_runtime.time.sleep", return_value=None)
    @mock.patch("agent_windows.voice_runtime.subprocess.Popen")
    @mock.patch("agent_windows.voice_runtime.shutil.which")
    def test_sapi_fallback_uses_fixed_powershell_program(self, which, popen, _sleep):
        which.side_effect = lambda name: None if name == "ffplay" else r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
        popen.return_value = _Process()
        started = []
        with mock.patch("agent_windows.voice_runtime.sys.platform", "win32"):
            self.service().speak("שלום; $(Remove-Item C:\\*)", on_audio_start=lambda: started.append(True))
        command = popen.call_args.args[0]
        self.assertIn("-LiteralPath $args[0]", command)
        self.assertNotIn("שלום", " ".join(command))
        self.assertEqual(started, [True])

    @mock.patch("agent_windows.voice_runtime.time.sleep", return_value=None)
    @mock.patch("agent_windows.voice_runtime.subprocess.Popen")
    @mock.patch("agent_windows.voice_runtime.shutil.which")
    def test_sapi_playback_is_interruptible(self, which, popen, _sleep):
        which.return_value = "powershell.exe"
        process = _Process()
        popen.return_value = process
        cancelled = threading.Event()
        cancelled.set()
        with mock.patch("agent_windows.voice_runtime.sys.platform", "win32"):
            self.assertFalse(self.service()._speak_local_sapi("text", cancel_event=cancelled))
        popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
