from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys

from .audio.encoder import FFmpegCapabilities


def total_memory_bytes() -> int | None:
    try:
        if sys.platform.startswith("win"):
            import ctypes
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong),("dwMemoryLoad",ctypes.c_ulong),("ullTotalPhys",ctypes.c_ulonglong),
                            ("ullAvailPhys",ctypes.c_ulonglong),("ullTotalPageFile",ctypes.c_ulonglong),("ullAvailPageFile",ctypes.c_ulonglong),
                            ("ullTotalVirtual",ctypes.c_ulonglong),("ullAvailVirtual",ctypes.c_ulonglong),("sullAvailExtendedVirtual",ctypes.c_ulonglong)]
            value = MEMORYSTATUSEX()
            value.dwLength = ctypes.sizeof(value)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(value)):
                return None
            return value.ullTotalPhys
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, OSError, ValueError):
        return None


def collect(runtime) -> dict:
    codecs = FFmpegCapabilities().supported_codecs()
    configured = {provider.name: provider.is_available() for provider in runtime.provider_manager.providers}
    return {
        "python": platform.python_version(), "platform": platform.platform(), "windows_compatible": sys.platform.startswith("win") and sys.version_info >= (3,11),
        "cpu": platform.processor() or platform.machine(), "ram_bytes": total_memory_bytes(),
        "ffmpeg": bool(shutil.which("ffmpeg")), "ffplay": bool(shutil.which("ffplay")), "libopus": "ogg_opus" in codecs,
        "providers": configured, "provider_health": {k: vars(v) for k,v in runtime.provider_manager.health.items()},
        "relay_configured": runtime.relay.is_available() if runtime.relay else False,
        "relay_reachable": runtime.relay.health() if runtime.relay else False,
        "memory_backend": type(runtime.memory).__name__, "network_state": runtime.network.state.value,
        "stt": {p.name:p.is_available() for p in runtime.stt.providers},
        "tts": {runtime.tts.name:runtime.tts.is_available()} if runtime.tts else {},
        "microphone_mode": "FFmpeg DirectShow" if sys.platform.startswith("win") else "unavailable on this OS",
        "local_llm": any(p.name == "local" and p.is_available() for p in runtime.provider_manager.providers),
        "llmfit": bool(shutil.which("llmfit")), "audio_codecs": sorted(codecs),
    }


def run_llmfit() -> str:
    executable = shutil.which("llmfit")
    if not executable: return "llmfit is not installed; no model was downloaded."
    try:
        result = subprocess.run([executable,"recommend","--json"],capture_output=True,text=True,timeout=30,check=False)
    except subprocess.TimeoutExpired:
        return "llmfit timed out after 30 seconds."
    except OSError as exc:
        return f"llmfit could not start: {exc}"
    return result.stdout if result.returncode == 0 else "llmfit failed: " + result.stderr[:300]
