from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import urllib.request
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

DEFAULT_UPDATE_URL = (
    "https://github.com/csivr2ivr2-lgtm/agent-Windows/releases/latest/download/update.json"
)


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    url: str
    sha256: str
    mandatory: bool = False
    notes: str = ""


def current_version() -> str:
    try:
        return metadata.version("agent-windows")
    except metadata.PackageNotFoundError:
        return "0.0.0"


def _version_tuple(value: str) -> tuple[int, ...]:
    clean = value.strip().lstrip("vV").split("+", 1)[0].split("-", 1)[0]
    result: list[int] = []
    for part in clean.split("."):
        digits = "".join(char for char in part if char.isdigit())
        result.append(int(digits or "0"))
    while len(result) < 3:
        result.append(0)
    return tuple(result)


def is_newer(candidate: str, installed: str) -> bool:
    return _version_tuple(candidate) > _version_tuple(installed)


def check_for_update(url: str | None = None, *, timeout: float = 5.0) -> UpdateInfo | None:
    endpoint = url or os.getenv("AGENT_UPDATE_URL", "").strip() or DEFAULT_UPDATE_URL
    request = urllib.request.Request(
        endpoint,
        headers={"Accept": "application/json", "User-Agent": "AI-Aharon-Updater/1"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        payload = json.loads(response.read().decode("utf-8"))
    info = UpdateInfo(
        version=str(payload["version"]).strip().lstrip("vV"),
        url=str(payload["url"]).strip(),
        sha256=str(payload["sha256"]).strip().casefold(),
        mandatory=bool(payload.get("mandatory", False)),
        notes=str(payload.get("notes", "")).strip(),
    )
    if not info.url.lower().startswith("https://"):
        raise ValueError("update installer URL must use HTTPS")
    if len(info.sha256) != 64 or any(c not in "0123456789abcdef" for c in info.sha256):
        raise ValueError("invalid update SHA-256")
    return info if is_newer(info.version, current_version()) else None


def download_update(info: UpdateInfo, *, timeout: float = 60.0) -> Path:
    target = Path(tempfile.gettempdir()) / f"AI-Aharon-Setup-{info.version}.exe"
    partial = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(info.url, headers={"User-Agent": "AI-Aharon-Updater/1"})
    digest = hashlib.sha256()
    with urllib.request.urlopen(request, timeout=timeout) as response, partial.open("wb") as output:  # noqa: S310
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            output.write(chunk)
            digest.update(chunk)
    actual = digest.hexdigest().casefold()
    if actual != info.sha256:
        partial.unlink(missing_ok=True)
        raise ValueError("downloaded installer failed SHA-256 verification")
    os.replace(partial, target)
    return target


def launch_installer(path: str | Path, *, silent: bool = False) -> None:
    installer = str(Path(path).resolve())
    args = [installer]
    if silent:
        args.extend(["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"])
    subprocess.Popen(args, close_fds=True)  # noqa: S603
