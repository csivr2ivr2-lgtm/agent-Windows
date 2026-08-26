from __future__ import annotations

import os
import sys
import threading
from pathlib import Path


SERVICE_NAME = "AgentWindowsAI"
SERVICE_CLASS_STRING = "agent_windows.windows_service.AgentWindowsService"


def _project_root() -> Path:
    configured = os.getenv("AGENT_WINDOWS_HOME", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


_PYWIN32_IMPORT_ERROR: Exception | None = None

if sys.platform.startswith("win"):
    try:
        import servicemanager
        import win32event
        import win32service
        import win32serviceutil
    except ImportError as exc:  # pragma: no cover - depends on Windows runtime
        _PYWIN32_IMPORT_ERROR = exc
    else:
        # IMPORTANT: this class must live at module scope. pywin32 stores the
        # import path in the Windows Service registry and imports it in a new
        # pythonservice.exe process. A class nested inside main() cannot be
        # imported by the Service Control Manager process and causes error 1053.
        class AgentWindowsService(win32serviceutil.ServiceFramework):
            _svc_name_ = SERVICE_NAME
            _svc_display_name_ = "Agent Windows AI"
            _svc_description_ = (
                "Background AI runtime for Agent Windows. "
                "Audio stays in the logged-in user session."
            )

            def __init__(self, args):
                super().__init__(args)
                self.stop_event = win32event.CreateEvent(None, 0, 0, None)
                self.backend = None

            def SvcStop(self):
                self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
                if self.backend is not None:
                    self.backend.stop()
                win32event.SetEvent(self.stop_event)

            def SvcDoRun(self):
                from .config import Settings
                from .logging_utils import configure_logging
                from .runtime import AgentRuntime
                from .service_api import ServiceBackend

                root = _project_root()
                os.environ.setdefault("AGENT_WINDOWS_HOME", str(root))
                os.chdir(root)
                settings = Settings.from_env(root / ".env")
                configure_logging(settings.log_level)
                servicemanager.LogInfoMsg("Agent Windows AI service starting")
                try:
                    with AgentRuntime(settings) as runtime:
                        self.backend = ServiceBackend(runtime, settings.data_dir)
                        worker = threading.Thread(
                            target=self.backend.serve_forever,
                            name="agent-windows-service-api",
                            daemon=True,
                        )
                        worker.start()
                        win32event.WaitForSingleObject(
                            self.stop_event, win32event.INFINITE
                        )
                        self.backend.stop()
                        worker.join(timeout=5)
                except Exception:
                    servicemanager.LogErrorMsg(
                        "Agent Windows AI service crashed:\n" + _format_current_exception()
                    )
                    raise
                finally:
                    servicemanager.LogInfoMsg("Agent Windows AI service stopped")


def _format_current_exception() -> str:
    import traceback

    return "".join(traceback.format_exc())[-7000:]


def _run_service_command_line() -> int:
    if not sys.platform.startswith("win"):
        print("Windows service support is only available on Windows.", file=sys.stderr)
        return 2
    if _PYWIN32_IMPORT_ERROR is not None:
        print(
            "pywin32 is required. Run: .\\.venv\\Scripts\\python.exe -m pip install -e .",
            file=sys.stderr,
        )
        return 2

    service_class = globals().get("AgentWindowsService")
    if service_class is None:
        print("Windows service class is unavailable.", file=sys.stderr)
        return 2

    # When this module is launched with ``python -m``, pywin32 otherwise
    # derives the service class from ``__main__`` / argv[0] and stores a file
    # path-like value in HKLM\...\PythonClass. pythonservice.exe cannot import
    # that value when SCM starts the service, which surfaces as error 1053.
    # Register the stable import path explicitly.
    win32serviceutil.HandleCommandLine(
        service_class, serviceClassString=SERVICE_CLASS_STRING
    )
    return 0


def main() -> int:
    return _run_service_command_line()


if __name__ == "__main__":
    raise SystemExit(main())
