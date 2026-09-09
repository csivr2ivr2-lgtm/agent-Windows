from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from agent_windows import first_run


class _Root:
    def __init__(self):
        self.withdrawn = False
        self.quit_called = False
        self.destroyed = False

    def withdraw(self):
        self.withdrawn = True

    def after(self, _delay, callback):
        callback()

    def quit(self):
        self.quit_called = True

    def mainloop(self):
        return None

    def destroy(self):
        self.destroyed = True


class _Window:
    def __init__(self):
        self.protocols = {}
        self.destroyed = False
        self.updated = False
        self.deiconified = False
        self.lifted = False
        self.focused = False

    def protocol(self, name, callback):
        self.protocols[name] = callback

    def destroy(self):
        self.destroyed = True

    def update_idletasks(self):
        self.updated = True

    def deiconify(self):
        self.deiconified = True

    def lift(self):
        self.lifted = True

    def focus_force(self):
        self.focused = True


class FirstRunTests(unittest.TestCase):
    def test_desktop_args_include_minimized(self):
        env = Path("C:/state/.env")
        self.assertEqual(first_run._desktop_args(env, True), ["--env", str(env), "--minimized"])
        self.assertEqual(first_run._desktop_args(env, False), ["--env", str(env)])

    def test_existing_primary_key_starts_desktop_immediately(self):
        desktop_main = mock.Mock(return_value=7)
        fake_desktop = types.SimpleNamespace(main=desktop_main)
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            first_run, "read_env_file", return_value={"GROQ_API_KEY": "real-key"}
        ), mock.patch.dict(sys.modules, {"agent_windows.desktop_gui": fake_desktop}):
            env = Path(directory) / ".env"
            result = first_run.main(["--env", str(env), "--minimized"])

        self.assertEqual(result, 7)
        desktop_main.assert_called_once_with(["--env", str(env.resolve()), "--minimized"])

    def test_missing_key_opens_settings_then_continues_after_save(self):
        root = _Root()
        window = _Window()
        fake_tk = types.SimpleNamespace(Tk=lambda: root)
        desktop_main = mock.Mock(return_value=9)
        fake_desktop = types.SimpleNamespace(main=desktop_main)

        def show_settings(_root, _env_path, *, on_saved, on_cancel):
            self.assertIsNotNone(on_cancel)
            on_saved()
            return window

        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            first_run, "has_primary_provider_key", side_effect=[False, True]
        ), mock.patch.object(first_run, "read_env_file", return_value={}), mock.patch.object(
            first_run, "show_settings_window", side_effect=show_settings
        ), mock.patch.dict(
            sys.modules,
            {"tkinter": fake_tk, "agent_windows.desktop_gui": fake_desktop},
        ):
            env = Path(directory) / ".env"
            result = first_run.main(["--env", str(env)])

        self.assertEqual(result, 9)
        self.assertTrue(root.withdrawn)
        self.assertTrue(root.quit_called)
        self.assertTrue(root.destroyed)
        self.assertIn("WM_DELETE_WINDOW", window.protocols)
        self.assertTrue(window.updated)
        self.assertTrue(window.deiconified)
        self.assertTrue(window.lifted)
        self.assertTrue(window.focused)
        desktop_main.assert_called_once_with(["--env", str(env.resolve())])

    def test_closing_settings_without_key_exits_cleanly(self):
        root = _Root()
        window = _Window()
        fake_tk = types.SimpleNamespace(Tk=lambda: root)

        def mainloop_and_close():
            window.protocols["WM_DELETE_WINDOW"]()

        root.mainloop = mainloop_and_close
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            first_run, "has_primary_provider_key", return_value=False
        ), mock.patch.object(first_run, "read_env_file", return_value={}), mock.patch.object(
            first_run, "show_settings_window", return_value=window
        ), mock.patch.dict(sys.modules, {"tkinter": fake_tk}):
            result = first_run.main(["--env", str(Path(directory) / ".env")])

        self.assertEqual(result, 0)
        self.assertTrue(window.destroyed)
        self.assertTrue(root.quit_called)
        self.assertTrue(root.destroyed)


if __name__ == "__main__":
    unittest.main()
