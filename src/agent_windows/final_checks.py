from __future__ import annotations

import json
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

from .diagnostics import collect, provider_check_report, realtime_check_report
from .integrations import integrations_report
from .service_api import service_health
from .windows_service import SERVICE_NAME


def _windows_service_status() -> dict[str, Any]:
    if not sys.platform.startswith("win"):
        return {"status": "NOT_WINDOWS", "running": False, "automatic": False}

    def run(*args: str) -> str:
        try:
            completed = subprocess.run(
                ["sc.exe", *args],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"ERROR: {exc}"
        return (completed.stdout or completed.stderr or "").strip()

    query = run("query", SERVICE_NAME)
    config = run("qc", SERVICE_NAME)
    running = "RUNNING" in query.upper()
    automatic = "AUTO_START" in config.upper() or ("START_TYPE" in config.upper() and "2" in config)
    status = "RUNNING" if running else "STOPPED_OR_MISSING"
    return {
        "status": status,
        "running": running,
        "automatic": automatic,
        "query_excerpt": query[-1200:],
        "config_excerpt": config[-1200:],
    }


def _provider_snapshot(runtime) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for provider in runtime.provider_manager.providers:
        rows.append(
            {
                "provider": provider.name,
                "configured": bool(provider.is_available()),
                "status": "CONFIGURED" if provider.is_available() else "UNCONFIGURED",
            }
        )
    return rows


def build_final_report(runtime, *, live: bool = False) -> dict[str, Any]:
    diagnostics = collect(runtime)
    realtime = realtime_check_report(runtime)
    integrations = integrations_report(runtime)
    providers = provider_check_report(runtime) if live else _provider_snapshot(runtime)
    service = _windows_service_status()
    api_reachable = service_health(runtime.settings.data_dir) if sys.platform.startswith("win") else False

    blockers: list[str] = []
    external_validation: list[str] = []

    for key in ("streaming_stt", "streaming_llm", "streaming_tts", "barge_in", "microphone_persistent"):
        if not realtime.get(key):
            blockers.append(f"realtime.{key}")

    if sys.platform.startswith("win"):
        if not diagnostics.get("ffmpeg"):
            blockers.append("ffmpeg")
        if not diagnostics.get("ffplay"):
            blockers.append("ffplay")
        if not service.get("running"):
            blockers.append("windows_service.running")
        if not service.get("automatic"):
            blockers.append("windows_service.automatic")
        if not api_reachable:
            blockers.append("service_api.health")
    else:
        external_validation.append("Windows host validation")

    for item in integrations:
        status = str(item.get("status", "PENDING"))
        component = str(item.get("component", "unknown"))
        if status.startswith("PENDING"):
            blockers.append(f"integration.{component}")
        elif status in {"CODE_READY", "CONFIGURED"}:
            external_validation.append(f"integration.{component}:{status}")

    if live:
        for item in providers:
            status = str(item.get("status", ""))
            provider = str(item.get("provider", item.get("name", "unknown")))
            if status not in {"OK", "UNCONFIGURED"}:
                blockers.append(f"provider.{provider}:{status}")
            elif status == "UNCONFIGURED":
                external_validation.append(f"provider.{provider}:UNCONFIGURED")
    else:
        external_validation.append("live provider checks (--live)")

    if blockers:
        overall = "FAIL"
    elif external_validation:
        overall = "CODE_READY_EXTERNAL_VALIDATION_REQUIRED"
    else:
        overall = "PASS"

    summary = (
        f"{overall}: {len(blockers)} blocker(s), "
        f"{len(external_validation)} external/on-device validation item(s)"
    )
    return {
        "schema_version": 1,
        "product": "ai aharon",
        "overall": overall,
        "summary": summary,
        "host": {"platform": platform.platform(), "python": platform.python_version()},
        "diagnostics": diagnostics,
        "realtime": realtime,
        "providers": providers,
        "integrations": integrations,
        "windows_service": service,
        "service_api_reachable": api_reachable,
        "blockers": blockers,
        "external_validation_required": external_validation,
    }


def write_final_report(report: dict[str, Any], path: str | Path) -> Path:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    return target
