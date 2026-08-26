from __future__ import annotations

import os
import subprocess
from typing import Any


CREATE_NO_WINDOW = 0x08000000


def hidden_subprocess_kwargs(*, os_name: str | None = None) -> dict[str, Any]:
    """Return subprocess options that suppress console windows on Windows."""
    if (os_name or os.name) != "nt":
        return {}

    kwargs: dict[str, Any] = {"creationflags": CREATE_NO_WINDOW}
    startupinfo_factory = getattr(subprocess, "STARTUPINFO", None)
    if startupinfo_factory is not None:
        startupinfo = startupinfo_factory()
        startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0x00000001)
        startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
        kwargs["startupinfo"] = startupinfo
    return kwargs
