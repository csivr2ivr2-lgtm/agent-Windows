from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from urllib.parse import urlparse

_REPOSITORY = "csivr2ivr2-lgtm/agent-Windows"
DEFAULT_UPDATE_URL = f"https://github.com/{_REPOSITORY}/releases/latest/download/update.json"
_INSTALLER_NAME = re.compile(
    r"^AI-Aharon-Setup-[0-9]+\.[0-9]+\.[0-9]+(?:[.-][0-9A-Za-z.-]+)?\.exe$"
)
_GITHUB_DOWNLOAD_HOSTS = frozenset(
    {
        "github.com",
        "release-assets.githubusercontent.com",
        "objects.githubusercontent.com",
    }
)
_MAX_METADATA_BYTES = 64 * 1024
_MAX_INSTALLER_BYTES = 1024 * 1024 * 1024


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    url: str
    sha256: str
    mandatory: bool = False
    notes: str = ""


class _GitHubRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Allow redirects only to HTTPS hosts used by GitHub release assets."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlparse(newurl)
        host = (parsed.hostname or "").casefold()
        if (
            parsed.scheme != "https"
            or host not in _GITHUB_DOWNLOAD_HOSTS
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise urllib.error.HTTPError(
                newurl,
                code,
                "refusing unsafe update redirect",
                headers,
                fp,
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener():
    return urllib.request.build_opener(_GitHubRedirectHandler())


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
    """Accept only this project's HTTPS GitHub release endpoints."""
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
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


def _validate_final_download_url(value: str) -> None:
    parsed = urlparse(value)
    host = (parsed.hostname or "").casefold()
    if (
        parsed.scheme != "https"
        or host not in _GITHUB_DOWNLOAD_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError("GitHub redirected the update to an untrusted destination")


def check_for_update(url: str | None = None, *, timeout: float = 5.0) -> UpdateInfo | None:
    endpoint = _require_official_release_url(url or DEFAULT_UPDATE_URL, metadata_file=True)
    request = urllib.request.Request(
        endpoint,
        headers={"Accept": "application/json", "User-Agent": "AI-Aharon-Updater/1"},
    )
    with _opener().open(request, timeout=timeout) as response:
        _validate_final_download_url(response.geturl())
        raw = response.read(_MAX_METADATA_BYTES + 1)
    if len(raw) > _MAX_METADATA_BYTES:
        raise ValueError("update metadata is unexpectedly large")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("update metadata must be a JSON object")
    info = UpdateInfo(
        version=str(payload["version"]).strip().lstrip("vV"),
        url=str(payload["url"]).strip(),
        sha256=str(payload["sha256"]).strip().casefold(),
        mandatory=bool(payload.get("mandatory", False)),
        notes=str(payload.get("notes", "")).strip()[:4000],
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
    total = 0
    try:
        with _opener().open(request, timeout=timeout) as response, partial.open("wb") as output:
            _validate_final_download_url(response.geturl())
            declared = response.headers.get("Content-Length")
            if declared is not None and int(declared) > _MAX_INSTALLER_BYTES:
                raise ValueError("update installer is unexpectedly large")
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > _MAX_INSTALLER_BYTES:
                    raise ValueError("update installer exceeded the download limit")
                output.write(chunk)
                digest.update(chunk)
        if digest.hexdigest().casefold() != info.sha256:
            raise ValueError("downloaded installer failed SHA-256 verification")
        os.replace(partial, target)
        return target
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def launch_installer(path: str | Path) -> None:  # pragma: no cover - Windows-only launch
    candidate = Path(path).resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    if (
        candidate.parent != temp_root
        or not candidate.is_file()
        or not _INSTALLER_NAME.fullmatch(candidate.name)
    ):
        raise ValueError("refusing to launch an untrusted installer path")
    if os.name != "nt" or not hasattr(os, "startfile"):
        raise RuntimeError("the Windows installer can only be launched on Windows")
    os.startfile(str(candidate))  # type: ignore[attr-defined]
