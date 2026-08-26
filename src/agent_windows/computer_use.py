from __future__ import annotations

import importlib.util
import platform
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .windows_subprocess import hidden_subprocess_kwargs
from .windows_tools import FunctionTool

_TASK_NAME = re.compile(r"[^A-Za-z0-9_-]+")


class ComputerUseError(RuntimeError):
    pass


def _is_windows() -> bool:
    return platform.system().casefold() == "windows"


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, AttributeError, ValueError):
        return False


def _bounded_text(value: object, limit: int = 12000) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


@dataclass
class UFOExecutor:
    workdir: Path | None = None
    timeout: float = 180.0
    python_executable: str = sys.executable

    name = "ufo"

    def _runtime_python(self) -> str:
        if self.workdir:
            candidate = self.workdir / ".venv" / "Scripts" / "python.exe"
            if candidate.is_file():
                return str(candidate)
        return self.python_executable

    def is_available(self) -> bool:
        if not _is_windows():
            return False
        if _module_available("ufo"):
            return True
        return bool(self.workdir and (self.workdir / "ufo").is_dir() and Path(self._runtime_python()).is_file())

    def execute(self, task: str) -> dict[str, Any]:
        if not self.is_available():
            raise ComputerUseError("Microsoft UFO is not installed or this host is not Windows")
        request = task.strip()
        if not request:
            raise ValueError("computer task cannot be empty")
        task_name = _TASK_NAME.sub("-", request.casefold()).strip("-")[:48] or "ai-aharon-task"
        command = [
            self._runtime_python(),
            "-m",
            "ufo",
            "--task",
            task_name,
            "--request",
            request,
            "--log-level",
            "WARNING",
        ]
        try:
            result = subprocess.run(
                command,
                cwd=str(self.workdir) if self.workdir else None,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
                **hidden_subprocess_kwargs(),
            )
        except subprocess.TimeoutExpired as exc:
            raise ComputerUseError(f"UFO task exceeded {self.timeout:.0f}s timeout") from exc
        except OSError as exc:
            raise ComputerUseError(f"UFO could not start: {exc}") from exc
        if result.returncode != 0:
            detail = _bounded_text(result.stderr or result.stdout, 4000)
            raise ComputerUseError(f"UFO task failed ({result.returncode}): {detail}")
        return {
            "backend": self.name,
            "task": request,
            "task_name": task_name,
            "output": _bounded_text(result.stdout),
            "completed": True,
        }


@dataclass
class WindowsUseExecutor:
    model: str
    timeout: float = 180.0
    max_steps: int = 80

    name = "windows-use"

    def is_available(self) -> bool:
        return _is_windows() and bool(self.model.strip()) and _module_available("windows_use")

    def execute(self, task: str) -> dict[str, Any]:
        if not self.is_available():
            raise ComputerUseError("Windows-Use is not installed/configured or this host is not Windows")
        request = task.strip()
        if not request:
            raise ValueError("computer task cannot be empty")
        try:
            from windows_use import Agent
            from windows_use.providers.ollama import ChatOllama
        except ImportError as exc:
            raise ComputerUseError("Windows-Use runtime imports are unavailable") from exc

        try:
            llm = ChatOllama(model=self.model)
            try:
                agent = Agent(llm=llm, use_vision=False, max_steps=self.max_steps)
            except TypeError:
                agent = Agent(llm=llm, use_vision=False)
            started = time.monotonic()
            result = agent.invoke(task=request)
        except Exception as exc:
            raise ComputerUseError(f"Windows-Use task failed: {type(exc).__name__}: {exc}") from exc
        content = getattr(result, "content", result)
        return {
            "backend": self.name,
            "task": request,
            "output": _bounded_text(content),
            "duration_ms": round((time.monotonic() - started) * 1000, 1),
            "completed": True,
        }


class ComputerRouter:
    def __init__(self, ufo: UFOExecutor, windows_use: WindowsUseExecutor, *, backend: str = "auto"):
        normalized = backend.strip().casefold()
        if normalized not in {"auto", "ufo", "windows-use"}:
            raise ValueError("computer backend must be auto, ufo, or windows-use")
        self.ufo = ufo
        self.windows_use = windows_use
        self.backend = normalized

    def _ordered(self):
        if self.backend == "ufo":
            return (self.ufo,)
        if self.backend == "windows-use":
            return (self.windows_use,)
        return (self.ufo, self.windows_use)

    def execute(self, task: str) -> dict[str, Any]:
        failures: list[str] = []
        available = False
        for executor in self._ordered():
            if not executor.is_available():
                continue
            available = True
            try:
                return executor.execute(task)
            except ComputerUseError as exc:
                failures.append(f"{executor.name}: {exc}")
        if not available:
            raise ComputerUseError("no configured Windows computer-use backend is available")
        raise ComputerUseError("all Windows computer-use backends failed: " + "; ".join(failures))

    def status(self) -> dict[str, Any]:
        return {
            "selected": self.backend,
            "windows": _is_windows(),
            "ufo": {"available": self.ufo.is_available()},
            "windows_use": {"available": self.windows_use.is_available(), "model": self.windows_use.model},
        }


def build_computer_tools(router: ComputerRouter) -> list[FunctionTool]:
    def status(_args):
        return router.status()

    def execute(args):
        return router.execute(str(args["task"]))

    return [
        FunctionTool(
            "computer_status",
            "Inspect which Windows computer-use executors are available",
            {"type": "object", "properties": {}},
            status,
            risk="read_only",
        ),
        FunctionTool(
            "computer_task",
            "Execute a user-approved task through UFO with Windows-Use fallback",
            {"type": "object", "properties": {"task": {"type": "string"}}, "required": ["task"]},
            execute,
            risk="high",
        ),
    ]
