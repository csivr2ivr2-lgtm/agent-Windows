from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from urllib.parse import urlparse

_REPOSITORY = "csivr2ivr2-lgtm/agent-Windows"
DEFAULT_UPDATE_URL = f"https://github.com/{_REPOSITORY}/releases/latest/download/update.json"
DEFAULT_INSTALLER_URL = (
    f"https://github.com/{_REPOSITORY}/releases/latest/download/AI-Aharon-Setup.exe"
)
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[.-][0-9A-Za-z.-]+)?$")
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


def _validate_sha256(value: str) -> str:
    digest = value.strip().casefold()
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("invalid update SHA-256")
    return digest


def check_for_update(*, timeout: float = 5.0) -> UpdateInfo | None:
    request = urllib.request.Request(
        DEFAULT_UPDATE_URL,
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

    version = str(payload["version"]).strip().lstrip("vV")
    if not _VERSION.fullmatch(version):
        raise ValueError("invalid update version")
    if "url" in payload and str(payload["url"]).strip() != DEFAULT_INSTALLER_URL:
        raise ValueError("update metadata attempted to override the official installer URL")

    info = UpdateInfo(
        version=version,
        url=DEFAULT_INSTALLER_URL,
        sha256=_validate_sha256(str(payload["sha256"])),
        mandatory=bool(payload.get("mandatory", False)),
        notes=str(payload.get("notes", "")).strip()[:4000],
    )
    return info if is_newer(info.version, current_version()) else None


def download_update(info: UpdateInfo, *, timeout: float = 60.0) -> Path:
    if info.url != DEFAULT_INSTALLER_URL:
        raise ValueError("refusing a non-official installer URL")
    if not _VERSION.fullmatch(info.version):
        raise ValueError("invalid installer version")
    expected_sha = _validate_sha256(info.sha256)
    expected_name = f"AI-Aharon-Setup-{info.version}.exe"
    staging = Path(tempfile.mkdtemp(prefix="AI-Aharon-Update-"))
    target = staging / expected_name
    partial = staging / (expected_name + ".part")
    request = urllib.request.Request(
        DEFAULT_INSTALLER_URL, headers={"User-Agent": "AI-Aharon-Updater/1"}
    )
    digest = hashlib.sha256()
    total = 0
    try:
        with _opener().open(request, timeout=timeout) as response, partial.open("xb") as output:
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
        if digest.hexdigest().casefold() != expected_sha:
            raise ValueError("downloaded installer failed SHA-256 verification")
        os.replace(partial, target)
        return target
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def launch_installer(path: str | Path, *, expected_sha256: str) -> None:  # pragma: no cover - Windows-only launch
    candidate = Path(path).resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    if (
        candidate.parent.parent != temp_root
        or not candidate.parent.name.startswith("AI-Aharon-Update-")
        or not candidate.is_file()
        or not _INSTALLER_NAME.fullmatch(candidate.name)
    ):
        raise ValueError("refusing to launch an untrusted installer path")
    expected = _validate_sha256(expected_sha256)
    digest = hashlib.sha256()
    with candidate.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest().casefold() != expected:
        raise ValueError("installer changed after download verification")
    if os.name != "nt" or not hasattr(os, "startfile"):
        raise RuntimeError("the Windows installer can only be launched on Windows")
    os.startfile(str(candidate))  # type: ignore[attr-defined]
