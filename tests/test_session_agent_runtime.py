import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from agent_windows import session_agent


class FakeVoice:
    def __init__(self):
        self.spoken = []
    def listen(self): return "שלום"
    def speak(self, text): self.spoken.append(text)


class FakeRuntime:
    def __init__(self, _settings=None): self.voice = FakeVoice(); self.handled = []
    def handle_text(self, text): self.handled.append(text); return "local"
    def __enter__(self): return self
    def __exit__(self, *a): return None


class SessionAgentRuntimeTests(unittest.TestCase):
    def test_logging_hotkey_and_unregister(self):
        with tempfile.TemporaryDirectory() as tmp:
            session_agent._configure_file_logging(Path(tmp))
            self.assertTrue((Path(tmp) / "session-agent.log").exists())
        class User32:
            def RegisterHotKey(self, *a): return True
            def UnregisterHotKey(self, *a): self.unregistered = True
        user32 = User32()
        with mock.patch.object(session_agent.ctypes, "windll", types.SimpleNamespace(user32=user32), create=True):
            session_agent._register_hotkey(); session_agent._unregister_hotkey()
        self.assertTrue(user32.unregistered)
        class BadUser32(User32):
            def RegisterHotKey(self, *a): return False
            def UnregisterHotKey(self, *a): raise RuntimeError("x")
        with mock.patch.object(session_agent.ctypes, "windll", types.SimpleNamespace(user32=BadUser32()), create=True):
            with self.assertRaises(RuntimeError): session_agent._register_hotkey()
            session_agent._unregister_hotkey()

    def test_message_loop_service_and_fallback(self):
        runtime = FakeRuntime()
        settings = types.SimpleNamespace(data_dir=Path("data"))
        class Msg:
            message = 0
            wParam = 0
        class User32:
            def __init__(self): self.count = 0
            def GetMessageW(self, msg, *a):
                self.count += 1
                if self.count == 1:
                    msg.message = 123; msg.wParam = 0; return 1
                if self.count == 2:
                    msg.message = session_agent.WM_HOTKEY; msg.wParam = session_agent.HOTKEY_ID; return 1
                return 0
        with mock.patch.object(session_agent.ctypes, "windll", types.SimpleNamespace(user32=User32()), create=True), \
             mock.patch.object(session_agent.wintypes, "MSG", Msg), \
             mock.patch.object(session_agent.ctypes, "byref", lambda x: x), \
             mock.patch.object(session_agent, "service_health", return_value=True), \
             mock.patch.object(session_agent, "service_chat", return_value="svc"):
            session_agent._message_loop(runtime, settings)
        self.assertEqual(runtime.voice.spoken, ["svc"])

        runtime = FakeRuntime(); user32 = User32()
        with mock.patch.object(session_agent.ctypes, "windll", types.SimpleNamespace(user32=user32), create=True), \
             mock.patch.object(session_agent.wintypes, "MSG", Msg), \
             mock.patch.object(session_agent.ctypes, "byref", lambda x: x), \
             mock.patch.object(session_agent, "service_health", return_value=False):
            session_agent._message_loop(runtime, settings)
        self.assertEqual(runtime.handled, ["שלום"])
        self.assertEqual(runtime.voice.spoken, ["local"])

    def test_main_non_windows_and_windows_path(self):
        with mock.patch.object(session_agent.sys, "platform", "linux"):
            self.assertEqual(session_agent.main([]), 2)
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"; env.write_text("", encoding="utf-8")
            settings = types.SimpleNamespace(data_dir=Path(tmp) / "data", log_level="INFO")
            with mock.patch.object(session_agent.sys, "platform", "win32"), \
                 mock.patch.object(session_agent.Settings, "from_env", return_value=settings), \
                 mock.patch.object(session_agent, "configure_logging"), \
                 mock.patch.object(session_agent, "_configure_file_logging"), \
                 mock.patch.object(session_agent, "_register_hotkey"), \
                 mock.patch.object(session_agent, "_unregister_hotkey") as unreg, \
                 mock.patch.object(session_agent, "AgentRuntime", FakeRuntime), \
                 mock.patch.object(session_agent, "_message_loop") as loop, \
                 mock.patch.object(session_agent.os, "chdir"):
                self.assertEqual(session_agent.main(["--env", str(env)]), 0)
                loop.assert_called_once(); unreg.assert_called_once()


if __name__ == "__main__":
    unittest.main()
