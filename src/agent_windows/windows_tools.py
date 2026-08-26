from __future__ import annotations

import datetime as dt
import platform
import shutil
import heapq
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping


@dataclass
class FunctionTool:
    name: str
    description: str
    schema: Mapping[str, Any]
    function: Callable[[Mapping[str, Any]], Any]
    risk: str = "read_only"

    def invoke(self, arguments: Mapping[str, Any]) -> Any:
        return self.function(arguments)


def _safe_path(raw: str, roots: tuple[Path, ...]) -> Path:
    path = Path(raw).expanduser().resolve()
    if not any(path == root or root in path.parents for root in roots):
        raise PermissionError("path is outside configured allowed roots")
    return path


def build_windows_tools(allowed_roots: tuple[Path, ...]) -> list[FunctionTool]:
    roots = tuple(root.resolve() for root in allowed_roots)
    def now(_): return dt.datetime.now().astimezone().isoformat()
    def system(_):
        usage = shutil.disk_usage(Path.cwd())
        return {"os": platform.platform(), "machine": platform.machine(), "cpu": platform.processor(),
                "python": platform.python_version(), "disk_free_bytes": usage.free}
    def list_dir(args):
        path = _safe_path(str(args.get("path", ".")), roots)
        entries = heapq.nsmallest(200, path.iterdir(), key=lambda entry: entry.name.casefold())
        return [{"name": p.name, "type": "dir" if p.is_dir() else "file", "size": p.stat().st_size if p.is_file() else None}
                for p in entries]
    def read_text(args):
        path = _safe_path(str(args["path"]), roots)
        if path.stat().st_size > 1_000_000:
            raise ValueError("file exceeds 1 MB read limit")
        return path.read_text(encoding="utf-8", errors="replace")
    return [
        FunctionTool("current_datetime", "Get current local date and time from the Windows host clock", {"type":"object","properties":{}}, now),
        FunctionTool("current_time", "Backward-compatible alias for current_datetime", {"type":"object","properties":{}}, now),
        FunctionTool("system_info", "Get safe OS, CPU, Python and disk information", {"type":"object","properties":{}}, system),
        FunctionTool("list_directory", "List an allowed directory", {"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}, list_dir),
        FunctionTool("read_text_file", "Read a small UTF-8 text file in an allowed directory", {"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}, read_text),
    ]
