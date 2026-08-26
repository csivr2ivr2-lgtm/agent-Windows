import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

from agent_windows import desktop_gui
from agent_windows.realtime import RealtimeState
from agent_windows.voice_runtime import MicrophoneUnavailable


class DummyVar:
    def __init__(self, value=""):
        self.value = value

    def set(self, value):
        self.value = value


class DummyWidget:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.config = {}

    def pack(self, *args, **kwargs):
        return self

    def configure(self, *args, **kwargs):
        if args:
            self.config.setdefault("style_args", []).extend(args)
        self.config.update(kwargs)

    def create_oval(self, *args, **kwargs):
        return 1

    def create_text(self, *args, **kwargs):
        return 2


class DummyStyle(DummyWidget):
    def theme_names(self):
        return ("vista",)

    def theme_use(self, name):
        self.used = name


class DummyRoot(DummyWidget):
    def __init__(self):
        super().__init__()
        self.after_calls = []
        self.destroyed = False
        self.mainloop_called = False

    def title(self, value): self.title_value = value
    def geometry(self, value): self.geometry_value = value
    def minsize(self, *value): self.minsize_value = value
    def protocol(self, *value): self.protocol_value = value
    def after(self, delay, callback):
        self.after_calls.append((delay, callback))
        return len(self.after_calls)
    def iconname(self, value): self.iconname_value = value
    def iconbitmap(self, **kwargs): self.iconbitmap_value = kwargs
    def update_idletasks(self): self.updated = True
    def winfo_id(self): return 99
    def deiconify(self): self.deiconified = True
    def lift(self): self.lifted = True
    def destroy(self): self.destroyed = True
    def iconify(self): self.iconified = True
    def mainloop(self): self.mainloop_called = True


class DummyRuntime:
    def __init__(self, _settings=None):
        self.closed = 0
        self.answers = []

    def handle_text(self, text):
        self.answers.append(text)
        return "local:" + text

    def close(self):
        self.closed += 1


class DummySettings:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.log_level = "INFO"


class ImmediateThread:
    def __init__(self, target=None, args=(), kwargs=None, **_):
        self.target = target
        self.args = args
        self.kwargs = kwargs or {}
        self.started = False

    def start(self):
        self.started = True

    def is_alive(self):
        return self.started


class FakeClosing:
    def __init__(self, *, set_value=False):
        self.value = set_value

    def is_set(self): return self.value
    def set(self): self.value = True
    def wait(self, _timeout): return True


class DesktopGuiRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tk = types.ModuleType("tkinter")
        self.ttk = types.SimpleNamespace(
            Style=DummyStyle,
            Frame=DummyWidget,
            Label=DummyWidget,
        )
        self.tk.ttk = self.ttk
        self.tk.Canvas = DummyWidget
        self.tk.Button = DummyWidget
        self.tk.StringVar = DummyVar
        self.tk.Tk = DummyRoot
        self.modules = mock.patch.dict(sys.modules, {"tkinter": self.tk})
        self.modules.start()

    def tearDown(self):
        self.modules.stop()

    def _app(self, tmp):
        root = DummyRoot()
        runtime = DummyRuntime()
        settings = DummySettings(tmp)
        with mock.patch.object(desktop_gui, "_apply_windows_icon"), \
             mock.patch.object(desktop_gui.AgentDesktopApp, "_start_health_monitor"), \
             mock.patch.object(desktop_gui.AgentDesktopApp, "_start_hotkey_listener"), \
             mock.patch.object(desktop_gui.AgentDesktopApp, "_tick_timer"):
            app = desktop_gui.AgentDesktopApp(root, runtime, settings, auto_start=True)
        return app, root, runtime, settings

    def test_build_ui_status_answer_timer_and_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, root, runtime, settings = self._app(tmp)
            self.assertEqual(root.title_value, "ai aharon")
            self.assertTrue(root.after_calls)
            app._set_status("x")
            self.assertEqual(app.status_var.value, "x")
            app._set_service_status(True)
            self.assertIn("מחובר", app.service_label.config["text"])
            app._set_service_status(False)
            self.assertIn("לא זמין", app.service_label.config["text"])

            with mock.patch.object(desktop_gui, "service_health", return_value=True), \
                 mock.patch.object(desktop_gui, "service_chat", return_value="svc"):
                self.assertEqual(app._answer("hello"), "svc")
            with mock.patch.object(desktop_gui, "service_health", return_value=False):
                self.assertEqual(app._answer("hello"), "local:hello")

            app._call_active.set()
            app._call_started_at = 100.0
            with mock.patch.object(desktop_gui.time, "monotonic", return_value=3761.0):
                app._tick_timer()
            self.assertEqual(app.timer_var.value, "01:01:01")

            app._show_and_start_call = mock.Mock()
            root.deiconify(); root.lift()
            self.assertTrue(root.deiconified and root.lifted)

            app.close()
            self.assertTrue(root.destroyed)
            self.assertEqual(runtime.closed, 1)
            app.close()
            self.assertEqual(runtime.closed, 1)

    def test_cross_thread_status_and_realtime_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, root, *_ = self._app(tmp)
            t = threading.Thread(target=lambda: app._set_status("threaded"))
            t.start(); t.join()
            callback = root.after_calls[-1][1]
            callback()
            self.assertEqual(app.status_var.value, "threaded")
            for state in RealtimeState:
                app._on_realtime_state(state)

    def test_start_call_and_call_loop_success_and_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, _root, *_ = self._app(tmp)
            with mock.patch.object(desktop_gui.threading, "Thread", ImmediateThread):
                app.start_call()
                self.assertTrue(app._call_active.is_set())
                app.start_call()

            class Session:
                def __init__(self, runtime, status_callback):
                    self.status_callback = status_callback
                def run(self, keep_running):
                    self.status_callback(RealtimeState.LISTENING)

            app._call_active.set()
            with mock.patch.object(desktop_gui, "LocalRealtimeSession", Session):
                app._call_loop()
            self.assertFalse(app._call_active.is_set())

            class MicFail(Session):
                def run(self, keep_running):
                    raise MicrophoneUnavailable("missing")
            app._call_active.set()
            with mock.patch.object(desktop_gui, "LocalRealtimeSession", MicFail):
                app._call_loop()
            self.assertEqual(app.status_var.value, desktop_gui.HEBREW_LABELS["error"])

            class GenericFail(Session):
                def run(self, keep_running):
                    raise RuntimeError("boom")
            app._call_active.set()
            with mock.patch.object(desktop_gui, "LocalRealtimeSession", GenericFail):
                app._call_loop()
            self.assertEqual(app.status_var.value, desktop_gui.HEBREW_LABELS["error"])

    def test_health_monitor_hotkey_and_icon_helpers(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, root, *_ = self._app(tmp)
            app._closing = FakeClosing()
            threads = []
            class CapturingThread(ImmediateThread):
                def __init__(self, *a, **k):
                    super().__init__(*a, **k); threads.append(self)
            with mock.patch.object(desktop_gui.threading, "Thread", CapturingThread), \
                 mock.patch.object(desktop_gui, "service_health", return_value=True):
                app._start_health_monitor()
            for thread in threads:
                thread.target(*thread.args, **thread.kwargs)

            class Msg:
                message = desktop_gui.WM_HOTKEY
                wParam = desktop_gui.HOTKEY_ID
            class User32:
                def __init__(self, register=True): self.register = register; self.calls = 0
                def RegisterHotKey(self, *a): return self.register
                def GetMessageW(self, *a):
                    self.calls += 1
                    return 1 if self.calls == 1 else 0
                def UnregisterHotKey(self, *a): self.unregistered = True
            threads.clear(); user32 = User32()
            app._closing = FakeClosing()
            with mock.patch.object(desktop_gui.threading, "Thread", CapturingThread), \
                 mock.patch.object(desktop_gui.ctypes, "windll", types.SimpleNamespace(user32=user32), create=True), \
                 mock.patch.object(desktop_gui.wintypes, "MSG", Msg), \
                 mock.patch.object(desktop_gui.ctypes, "byref", lambda x: x):
                app._start_hotkey_listener()
                threads[-1].target()
            self.assertTrue(user32.unregistered)

            threads.clear(); user32 = User32(register=False)
            with mock.patch.object(desktop_gui.threading, "Thread", CapturingThread), \
                 mock.patch.object(desktop_gui.ctypes, "windll", types.SimpleNamespace(user32=user32), create=True):
                app._start_hotkey_listener(); threads[-1].target()

            icon = Path(tmp) / "x.ico"; icon.write_bytes(b"ico")
            class LoadImage:
                restype = None
                def __call__(self, *a): return 7
            class IconUser32:
                LoadImageW = LoadImage()
                def SendMessageW(self, *a): self.sent = True
            with mock.patch.object(desktop_gui, "_icon_path", return_value=icon), \
                 mock.patch.object(desktop_gui.sys, "platform", "win32"), \
                 mock.patch.object(desktop_gui.ctypes, "windll", types.SimpleNamespace(user32=IconUser32()), create=True):
                desktop_gui._apply_windows_icon(root)

            with mock.patch.object(desktop_gui.sys, "platform", "win32"), \
                 mock.patch.object(desktop_gui.ctypes, "windll", types.SimpleNamespace(shell32=types.SimpleNamespace(SetCurrentProcessExplicitAppUserModelID=lambda x: None)), create=True):
                desktop_gui._set_windows_app_identity()
            with mock.patch.object(desktop_gui.sys, "platform", "linux"):
                desktop_gui._set_windows_app_identity()

    def test_logging_and_main_platform_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            desktop_gui._configure_file_logging(Path(tmp))
            self.assertTrue((Path(tmp) / "desktop-gui.log").exists())
            with mock.patch.object(desktop_gui.sys, "platform", "linux"):
                self.assertEqual(desktop_gui.main([]), 2)

            env = Path(tmp) / ".env"; env.write_text("", encoding="utf-8")
            root = DummyRoot(); settings = DummySettings(Path(tmp) / "data")
            with mock.patch.object(desktop_gui.sys, "platform", "win32"), \
                 mock.patch.object(desktop_gui.Settings, "from_env", return_value=settings), \
                 mock.patch.object(desktop_gui, "configure_logging"), \
                 mock.patch.object(desktop_gui, "_configure_file_logging"), \
                 mock.patch.object(desktop_gui, "_set_windows_app_identity"), \
                 mock.patch.object(self.tk, "Tk", return_value=root), \
                 mock.patch.object(desktop_gui, "AgentRuntime", return_value=DummyRuntime()), \
                 mock.patch.object(desktop_gui, "AgentDesktopApp") as app_cls, \
                 mock.patch.object(desktop_gui.os, "chdir"):
                self.assertEqual(desktop_gui.main(["--env", str(env), "--minimized"]), 0)
                app_cls.assert_called_once()
                self.assertTrue(root.mainloop_called)
                self.assertTrue(root.after_calls)


if __name__ == "__main__":
    unittest.main()
