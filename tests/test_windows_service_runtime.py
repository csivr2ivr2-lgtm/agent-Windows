import importlib.util
import io
import os
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import agent_windows.windows_service as windows_service


class WindowsServiceTests(unittest.TestCase):
    def test_project_root_env_programdata_and_developer_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.dict(os.environ, {"AGENT_WINDOWS_HOME": str(root)}, clear=False):
                self.assertEqual(windows_service._project_root(), root.resolve())

        with tempfile.TemporaryDirectory() as directory:
            machine = Path(directory) / "AgentWindowsAI"
            machine.mkdir()
            (machine / ".env").write_text("X=1", encoding="utf-8")
            with mock.patch.dict(
                os.environ,
                {"AGENT_WINDOWS_HOME": "", "PROGRAMDATA": directory},
                clear=False,
            ):
                self.assertEqual(windows_service._project_root(), machine.resolve())

        with mock.patch.dict(
            os.environ,
            {"AGENT_WINDOWS_HOME": "", "PROGRAMDATA": ""},
            clear=False,
        ):
            expected = Path(windows_service.__file__).resolve().parents[2]
            self.assertEqual(windows_service._project_root(), expected)

    def test_format_current_exception_contains_recent_traceback(self):
        try:
            raise RuntimeError("service-boom")
        except RuntimeError:
            text = windows_service._format_current_exception()
        self.assertIn("service-boom", text)
        self.assertLessEqual(len(text), 7000)

    def test_command_line_rejects_non_windows(self):
        stderr = io.StringIO()
        with mock.patch.object(windows_service.sys, "platform", "linux"), redirect_stderr(stderr):
            self.assertEqual(windows_service._run_service_command_line(), 2)
        self.assertIn("only available on Windows", stderr.getvalue())

    def test_command_line_reports_missing_pywin32(self):
        stderr = io.StringIO()
        with (
            mock.patch.object(windows_service.sys, "platform", "win32"),
            mock.patch.object(windows_service, "_PYWIN32_IMPORT_ERROR", ImportError("no pywin32")),
            redirect_stderr(stderr),
        ):
            self.assertEqual(windows_service._run_service_command_line(), 2)
        self.assertIn("pywin32 is required", stderr.getvalue())

    def test_command_line_reports_missing_service_class(self):
        stderr = io.StringIO()
        with (
            mock.patch.object(windows_service.sys, "platform", "win32"),
            mock.patch.object(windows_service, "_PYWIN32_IMPORT_ERROR", None),
            mock.patch.dict(windows_service.__dict__, {"AgentWindowsService": None}),
            redirect_stderr(stderr),
        ):
            self.assertEqual(windows_service._run_service_command_line(), 2)
        self.assertIn("class is unavailable", stderr.getvalue())

    def _load_windows_variant(self):
        service_manager = types.ModuleType("servicemanager")
        service_manager.info = []
        service_manager.errors = []
        service_manager.LogInfoMsg = service_manager.info.append
        service_manager.LogErrorMsg = service_manager.errors.append

        win32event = types.ModuleType("win32event")
        win32event.INFINITE = -1
        win32event.events = []
        win32event.CreateEvent = lambda *_args: object()
        win32event.SetEvent = win32event.events.append
        win32event.waits = []
        win32event.WaitForSingleObject = lambda event, timeout: win32event.waits.append((event, timeout))

        win32service = types.ModuleType("win32service")
        win32service.SERVICE_STOP_PENDING = 3

        win32serviceutil = types.ModuleType("win32serviceutil")

        class ServiceFramework:
            def __init__(self, args):
                self.args = args
                self.statuses = []

            def ReportServiceStatus(self, status):
                self.statuses.append(status)

        win32serviceutil.ServiceFramework = ServiceFramework
        win32serviceutil.command_calls = []
        win32serviceutil.HandleCommandLine = (
            lambda service_class, **kwargs: win32serviceutil.command_calls.append((service_class, kwargs))
        )

        modules = {
            "servicemanager": service_manager,
            "win32event": win32event,
            "win32service": win32service,
            "win32serviceutil": win32serviceutil,
        }
        path = Path(windows_service.__file__)
        module_name = "agent_windows._windows_service_test_variant"
        spec = importlib.util.spec_from_file_location(module_name, path)
        module = importlib.util.module_from_spec(spec)
        with mock.patch.object(sys, "platform", "win32"), mock.patch.dict(sys.modules, modules):
            sys.modules[module_name] = module
            assert spec.loader is not None
            spec.loader.exec_module(module)
        return module, service_manager, win32event, win32service, win32serviceutil

    def test_windows_variant_command_line_and_stop(self):
        module, _manager, event, service, util = self._load_windows_variant()
        with mock.patch.object(module.sys, "platform", "win32"):
            self.assertEqual(module._run_service_command_line(), 0)
        self.assertEqual(len(util.command_calls), 1)
        service_class, kwargs = util.command_calls[0]
        self.assertIs(service_class, module.AgentWindowsService)
        self.assertEqual(kwargs["serviceClassString"], module.SERVICE_CLASS_STRING)

        instance = module.AgentWindowsService(["service"])
        backend = SimpleNamespace(stop=mock.Mock())
        instance.backend = backend
        instance.SvcStop()
        self.assertEqual(instance.statuses, [service.SERVICE_STOP_PENDING])
        backend.stop.assert_called_once_with()
        self.assertEqual(event.events, [instance.stop_event])

        instance2 = module.AgentWindowsService([])
        instance2.SvcStop()
        self.assertEqual(event.events[-1], instance2.stop_event)

    def test_windows_variant_service_run_success_and_crash_logging(self):
        module, manager, event, _service, _util = self._load_windows_variant()
        original_cwd = Path.cwd()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                data_dir = root / "data"
                settings = SimpleNamespace(log_level="INFO", data_dir=data_dir)
                fake_runtime = mock.MagicMock()
                fake_runtime.__enter__.return_value = object()
                fake_runtime.__exit__.return_value = False
                backend = SimpleNamespace(
                    serve_forever=mock.Mock(),
                    stop=mock.Mock(),
                )
                with (
                    mock.patch.object(module, "_project_root", return_value=root),
                    mock.patch("agent_windows.config.Settings.from_env", return_value=settings),
                    mock.patch("agent_windows.logging_utils.configure_logging") as configure,
                    mock.patch("agent_windows.runtime.AgentRuntime", return_value=fake_runtime),
                    mock.patch("agent_windows.service_api.ServiceBackend", return_value=backend),
                ):
                    instance = module.AgentWindowsService([])
                    instance.SvcDoRun()
                configure.assert_called_once_with("INFO")
                backend.serve_forever.assert_called_once_with()
                backend.stop.assert_called_once_with()
                self.assertEqual(event.waits, [(instance.stop_event, event.INFINITE)])
                self.assertTrue(any("starting" in message for message in manager.info))
                self.assertTrue(any("stopped" in message for message in manager.info))

                with (
                    mock.patch.object(module, "_project_root", return_value=root),
                    mock.patch("agent_windows.config.Settings.from_env", return_value=settings),
                    mock.patch("agent_windows.logging_utils.configure_logging"),
                    mock.patch("agent_windows.runtime.AgentRuntime", side_effect=RuntimeError("runtime-fail")),
                ):
                    instance = module.AgentWindowsService([])
                    with self.assertRaises(RuntimeError):
                        instance.SvcDoRun()
                self.assertTrue(any("runtime-fail" in message for message in manager.errors))
        finally:
            os.chdir(original_cwd)

    def test_main_delegates(self):
        with mock.patch.object(windows_service, "_run_service_command_line", return_value=7) as run:
            self.assertEqual(windows_service.main(), 7)
            run.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
