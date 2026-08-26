from __future__ import annotations

import ctypes
import json
import os
import platform
import re
import subprocess
from dataclasses import asdict, dataclass
from urllib.error import URLError
from urllib.request import Request, urlopen

from .windows_subprocess import hidden_subprocess_kwargs

_GIB = 1024 ** 3
_MODEL_SIZE = re.compile(r"(?<!\d)(\d+(?:\.\d+)?)\s*[bB](?![A-Za-z])")
_QUANT_BITS = {
    "q2": 2.5,
    "q3": 3.5,
    "q4": 4.5,
    "q5": 5.5,
    "q6": 6.5,
    "q8": 8.5,
    "fp8": 8.0,
    "f16": 16.0,
    "fp16": 16.0,
    "bf16": 16.0,
    "f32": 32.0,
    "fp32": 32.0,
}


@dataclass(frozen=True)
class HardwareSnapshot:
    os: str
    machine: str
    cpu: str
    cpu_count: int
    memory_total_bytes: int | None
    memory_available_bytes: int | None
    gpu_names: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ModelFit:
    model: str
    parameter_billions: float
    quantization: str
    context_tokens: int
    estimated_required_bytes: int
    usable_memory_bytes: int | None
    headroom_bytes: int | None
    score: int
    verdict: str
    notes: tuple[str, ...]

    def as_dict(self) -> dict:
        return asdict(self)


def _linux_memory() -> tuple[int | None, int | None]:
    total = available = None
    try:
        values: dict[str, int] = {}
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                key, _, value = line.partition(":")
                if key in {"MemTotal", "MemAvailable"}:
                    values[key] = int(value.strip().split()[0]) * 1024
        total = values.get("MemTotal")
        available = values.get("MemAvailable")
    except (OSError, ValueError, IndexError):
        pass
    if total is None:
        try:
            total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
            available = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_AVPHYS_PAGES")
        except (AttributeError, OSError, ValueError):
            pass
    return total, available


def _windows_memory() -> tuple[int | None, int | None]:
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MEMORYSTATUSEX()
    status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    try:
        kernel32 = ctypes.windll.kernel32
        if not kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None, None
    except (AttributeError, OSError):
        return None, None
    return int(status.ullTotalPhys), int(status.ullAvailPhys)


def _windows_gpu_names() -> tuple[str, ...]:
    if not sys_platform_is_windows():
        return ()
    command = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name",
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
            **hidden_subprocess_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return ()
    if result.returncode != 0:
        return ()
    return tuple(line.strip() for line in result.stdout.splitlines() if line.strip())


def sys_platform_is_windows() -> bool:
    return platform.system().casefold() == "windows"


def hardware_snapshot(*, include_gpu: bool = True) -> HardwareSnapshot:
    if sys_platform_is_windows():
        total, available = _windows_memory()
    else:
        total, available = _linux_memory()
    return HardwareSnapshot(
        os=platform.platform(),
        machine=platform.machine(),
        cpu=platform.processor() or platform.machine(),
        cpu_count=os.cpu_count() or 1,
        memory_total_bytes=total,
        memory_available_bytes=available,
        gpu_names=_windows_gpu_names() if include_gpu else (),
    )


def quantization_bits(quantization: str) -> float:
    value = quantization.strip().casefold().replace("_", "")
    for prefix, bits in _QUANT_BITS.items():
        if value.startswith(prefix):
            return bits
    match = re.search(r"(\d+(?:\.\d+)?)", value)
    if match:
        bits = float(match.group(1))
        if 1.0 <= bits <= 32.0:
            return bits
    return 4.5


def estimate_model_bytes(
    parameter_billions: float,
    quantization: str = "q4",
    context_tokens: int = 8192,
) -> int:
    if parameter_billions <= 0:
        raise ValueError("parameter_billions must be positive")
    if context_tokens < 1:
        raise ValueError("context_tokens must be positive")
    bits = quantization_bits(quantization)
    weight_bytes = parameter_billions * 1_000_000_000 * (bits / 8.0)
    runtime_overhead = weight_bytes * 0.18
    context_bytes = parameter_billions * _GIB * 0.30 * (context_tokens / 8192.0)
    fixed_overhead = 320 * 1024 * 1024
    return int(weight_bytes + runtime_overhead + context_bytes + fixed_overhead)


def assess_model_fit(
    model: str,
    parameter_billions: float,
    *,
    quantization: str = "q4",
    context_tokens: int = 8192,
    hardware: HardwareSnapshot | None = None,
) -> ModelFit:
    hardware = hardware or hardware_snapshot()
    required = estimate_model_bytes(parameter_billions, quantization, context_tokens)
    memory_basis = hardware.memory_available_bytes or hardware.memory_total_bytes
    notes = ["estimate includes weights, runtime buffers, context/KV allowance and fixed overhead"]
    if memory_basis is None:
        score, verdict, headroom = 50, "UNKNOWN", None
        notes.append("host memory could not be detected")
    else:
        reserve = min(int(2.0 * _GIB), int(memory_basis * 0.30))
        usable = max(0, memory_basis - reserve)
        headroom = usable - required
        ratio = usable / required if required else 0.0
        if ratio >= 1.35:
            score, verdict = 100, "COMFORTABLE"
        elif ratio >= 1.10:
            score, verdict = 85, "FITS"
        elif ratio >= 0.95:
            score, verdict = 65, "TIGHT"
        elif ratio >= 0.75:
            score, verdict = 35, "RISKY"
        else:
            score, verdict = 10, "DOES_NOT_FIT"
        notes.append(f"reserved {reserve / _GIB:.2f} GiB for OS and ai aharon runtime")
        memory_basis = usable
    return ModelFit(
        model=model,
        parameter_billions=float(parameter_billions),
        quantization=quantization,
        context_tokens=int(context_tokens),
        estimated_required_bytes=required,
        usable_memory_bytes=memory_basis,
        headroom_bytes=headroom,
        score=score,
        verdict=verdict,
        notes=tuple(notes),
    )


def infer_parameter_billions(name: str) -> float | None:
    matches = _MODEL_SIZE.findall(name.replace("_", "-"))
    if not matches:
        return None
    try:
        return float(matches[-1])
    except (TypeError, ValueError):
        return None


def _ollama_tags(base_url: str, timeout: float = 1.5) -> list[dict]:
    url = base_url.rstrip("/")
    if url.endswith("/v1"):
        url = url[:-3]
    req = Request(url + "/api/tags", headers={"Accept": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
    except (OSError, URLError):
        return []
    if len(raw) > 2 * 1024 * 1024:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    models = data.get("models") if isinstance(data, dict) else None
    return [item for item in models or [] if isinstance(item, dict)]


def installed_ollama_fits(
    base_url: str = "http://127.0.0.1:11434/v1",
    *,
    hardware: HardwareSnapshot | None = None,
) -> list[ModelFit]:
    hardware = hardware or hardware_snapshot()
    fits: list[ModelFit] = []
    for item in _ollama_tags(base_url):
        name = str(item.get("name") or item.get("model") or "unknown")
        details = item.get("details") if isinstance(item.get("details"), dict) else {}
        params = infer_parameter_billions(name) or infer_parameter_billions(
            str(details.get("parameter_size") or "")
        )
        if params is None:
            continue
        quant = str(details.get("quantization_level") or "q4")
        fits.append(assess_model_fit(name, params, quantization=quant, hardware=hardware))
    return sorted(fits, key=lambda fit: (-fit.score, fit.estimated_required_bytes, fit.model.casefold()))


def model_fit_report(
    *,
    parameter_billions: float | None = None,
    quantization: str = "q4",
    context_tokens: int = 8192,
    model: str = "candidate",
    ollama_base_url: str = "http://127.0.0.1:11434/v1",
) -> dict:
    hardware = hardware_snapshot()
    report: dict = {"hardware": hardware.as_dict()}
    if parameter_billions is not None:
        report["candidate"] = assess_model_fit(
            model,
            parameter_billions,
            quantization=quantization,
            context_tokens=context_tokens,
            hardware=hardware,
        ).as_dict()
    installed = installed_ollama_fits(ollama_base_url, hardware=hardware)
    report["ollama_models"] = [fit.as_dict() for fit in installed]
    report["recommended_local_model"] = installed[0].model if installed else None
    return report
