from __future__ import annotations

import os
import sys
import threading
from pathlib import Path


def _project_root() -> Path:
    configured = os.getenv("AGENT_WINDOWS_HOME", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def _run_service_command_line() -> int:
    if not sys.platform.startswith("win"):
        print("Windows service support is only available on Windows.", file=sys.stderr)
        return 2
    try:
        import servicemanager
        import win32event
        import win32service
        import win32serviceutil
    except ImportError:
        print(
            "pywin32 is required. Run: .\\.venv\\Scripts\\python.exe -m pip install -e .",
            file=sys.stderr,
        )
        return 2

    from .config import Settings
    from .logging_utils import configure_logging
    from .runtime import AgentRuntime
    from .service_api import ServiceBackend

    class AgentWindowsService(win32serviceutil.ServiceFramework):
        _svc_name_ = "AgentWindowsAI"
        _svc_display_name_ = "Agent Windows AI"
        _svc_description_ = "Background AI runtime for Agent Windows. Audio stays in the logged-in user session."

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
            root = _project_root()
            os.chdir(root)
            settings = Settings.from_env(root / ".env")
            configure_logging(settings.log_level)
            servicemanager.LogInfoMsg("Agent Windows AI service starting")
            try:
                with AgentRuntime(settings) as runtime:
                    self.backend = ServiceBackend(runtime, settings.data_dir)
                    worker = threading.Thread(target=self.backend.serve_forever, daemon=True)
                    worker.start()
                    win32event.WaitForSingleObject(self.stop_event, win32event.INFINITE)
                    self.backend.stop()
                    worker.join(timeout=5)
            finally:
                servicemanager.LogInfoMsg("Agent Windows AI service stopped")

    win32serviceutil.HandleCommandLine(AgentWindowsService)
    return 0


def main() -> int:
    return _run_service_command_line()


if __name__ == "__main__":
    raise SystemExit(main())
