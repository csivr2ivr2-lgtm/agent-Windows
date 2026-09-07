from __future__ import annotations

import os
import sys
import threading
from dataclasses import replace
from pathlib import Path


SERVICE_NAME = "AgentWindowsAI"
SERVICE_CLASS_STRING = "agent_windows.windows_service.AgentWindowsService"


def _project_root() -> Path:
    """Return the writable machine state root used for config, memory, and logs."""
    configured = os.getenv("AGENT_WINDOWS_HOME", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()

    program_data = os.getenv("PROGRAMDATA", "").strip()
    if program_data:
        machine_root = Path(program_data) / "AgentWindowsAI"
        if (machine_root / ".env").is_file():
            return machine_root.resolve()

    # Developer / CLI fallback.
    return Path(__file__).resolve().parents[2]


def _install_root() -> Path:
    """Return the admin-protected program root containing runtime and media tools."""
    configured = os.getenv("AGENT_WINDOWS_INSTALL_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()

    executable = Path(sys.executable).resolve()
    if executable.parent.name.casefold() == "python-runtime":
        return executable.parent.parent

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
                "Low-privilege background AI runtime for Agent Windows. "
                "Audio and interactive computer use stay in the logged-in user session."
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

                state_root = _project_root()
                install_root = _install_root()
                previous_cwd = Path.cwd()
                os.environ.setdefault("AGENT_WINDOWS_HOME", str(state_root))
                os.environ.setdefault("AGENT_WINDOWS_INSTALL_ROOT", str(install_root))
                bundled_tools = install_root / "tools"
                if bundled_tools.is_dir():
                    os.environ["PATH"] = (
                        str(bundled_tools) + os.pathsep + os.environ.get("PATH", "")
                    )
                os.chdir(install_root)
                try:
                    settings = Settings.from_env(state_root / ".env")
                    if not settings.data_dir.is_absolute():
                        settings = replace(
                            settings,
                            data_dir=(state_root / settings.data_dir).resolve(),
                        )
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
                            "Agent Windows AI service crashed:\n"
                            + _format_current_exception()
                        )
                        raise
                    finally:
                        servicemanager.LogInfoMsg("Agent Windows AI service stopped")
                finally:
                    os.chdir(previous_cwd)


def _format_current_exception() -> str:
    import traceback

    return "".join(traceback.format_exc())[-7000:]


def _run_service_command_line() -> int:
    if not sys.platform.startswith("win"):
        print("Windows service mode is only available on Windows.", file=sys.stderr)
        return 2
    if _PYWIN32_IMPORT_ERROR is not None:
        print(f"pywin32 is required: {_PYWIN32_IMPORT_ERROR}", file=sys.stderr)
        return 2
    service_class = globals().get("AgentWindowsService")
    if service_class is None:
        print("Windows service class is unavailable.", file=sys.stderr)
        return 2
    import win32serviceutil

    win32serviceutil.HandleCommandLine(service_class)
    return 0


def main() -> int:
    return _run_service_command_line()


if __name__ == "__main__":
    raise SystemExit(main())
