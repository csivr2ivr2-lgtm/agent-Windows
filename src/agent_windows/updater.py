from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import urllib.request
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from urllib.parse import urlparse

_REPOSITORY = "csivr2ivr2-lgtm/agent-Windows"
DEFAULT_UPDATE_URL = f"https://github.com/{_REPOSITORY}/releases/latest/download/update.json"
_INSTALLER_NAME = re.compile(r"^AI-Aharon-Setup-[0-9]+\.[0-9]+\.[0-9]+(?:[.-][0-9A-Za-z.-]+)?\.exe$")


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


def _require_official_release_url(value: str, *, metadata_file: bool = False) -> str:
    """Accept only this project's HTTPS GitHub release endpoints.

    update.json is trusted only as release metadata for this repository. The installer URL
    is validated again before download so a modified feed cannot point the updater at an
    arbitrary executable.
    """
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname != "github.com" or parsed.query or parsed.fragment:
        raise ValueError("update URL must be an HTTPS GitHub release URL")
    prefix = f"/{_REPOSITORY}/releases/"
    if not parsed.path.startswith(prefix):
        raise ValueError("update URL must belong to the official AI Aharon repository")
    if metadata_file:
        if not parsed.path.endswith("/download/update.json"):
            raise ValueError("update metadata URL must point to update.json")
    else:
        name = Path(parsed.path).name
        if "/download/" not in parsed.path or not _INSTALLER_NAME.fullmatch(name):
            raise ValueError("update installer URL has an invalid release asset name")
    return value


def check_for_update(url: str | None = None, *, timeout: float = 5.0) -> UpdateInfo | None:
    endpoint = _require_official_release_url(url or DEFAULT_UPDATE_URL, metadata_file=True)
    request = urllib.request.Request(
        endpoint,
        headers={"Accept": "application/json", "User-Agent": "AI-Aharon-Updater/1"},
    )
    # Bandit B310 is intentionally suppressed: the URL is restricted above to one HTTPS host/path.
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310  # NOSONAR
        payload = json.loads(response.read().decode("utf-8"))
    info = UpdateInfo(
        version=str(payload["version"]).strip().lstrip("vV"),
        url=str(payload["url"]).strip(),
        sha256=str(payload["sha256"]).strip().casefold(),
        mandatory=bool(payload.get("mandatory", False)),
        notes=str(payload.get("notes", "")).strip(),
    )
    _require_official_release_url(info.url)
    if len(info.sha256) != 64 or any(c not in "0123456789abcdef" for c in info.sha256):
        raise ValueError("invalid update SHA-256")
    return info if is_newer(info.version, current_version()) else None


def download_update(info: UpdateInfo, *, timeout: float = 60.0) -> Path:
    _require_official_release_url(info.url)
    expected_name = f"AI-Aharon-Setup-{info.version}.exe"
    if Path(urlparse(info.url).path).name != expected_name:
        raise ValueError("installer asset version does not match update metadata")
    target = Path(tempfile.gettempdir()) / expected_name
    partial = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(info.url, headers={"User-Agent": "AI-Aharon-Updater/1"})
    digest = hashlib.sha256()
    # Bandit B310 is intentionally suppressed: the release URL is restricted above and the
    # resulting bytes are additionally pinned to the SHA-256 from update.json.
    with urllib.request.urlopen(request, timeout=timeout) as response, partial.open("wb") as output:  # nosec B310  # NOSONAR
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


def launch_installer(path: str | Path, *, silent: bool = False) -> None:  # pragma: no cover - Windows process launch
    candidate = Path(path).resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    if candidate.parent != temp_root or not candidate.is_file() or not _INSTALLER_NAME.fullmatch(candidate.name):
        raise ValueError("refusing to launch an untrusted installer path")
    args = [str(candidate)]
    if silent:
        args.extend(["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"])
    # The executable path is constrained to our updater-created temp asset and verified by SHA-256.
    subprocess.Popen(args, close_fds=True, shell=False)  # nosec B603  # NOSONAR
