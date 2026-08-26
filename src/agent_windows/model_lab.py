from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from .security import SecurityValidationError, resolve_within
from .windows_subprocess import hidden_subprocess_kwargs
from .windows_tools import FunctionTool


_JOB_ID = re.compile(r"^[0-9]{9,12}-(?:unsloth|soup)-[0-9a-f]{8}$")

_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"(?i)((?:api[_-]?key|token|secret|password|passphrase)\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"\b(?:sk|gsk|AIza)[A-Za-z0-9_.\-]{16,}\b"),
)


@dataclass(frozen=True)
class GPUInfo:
    name: str
    memory_mb: int


@dataclass(frozen=True)
class ModelLabStatus:
    unsloth_installed: bool
    soup_installed: bool
    soup_executable: str | None
    nvidia_gpus: tuple[GPUInfo, ...]
    training_compatible: bool
    reason: str

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["nvidia_gpus"] = [asdict(gpu) for gpu in self.nvidia_gpus]
        return data


@dataclass(frozen=True)
class ModelLabJob:
    job_id: str
    backend: str
    job_dir: str
    dataset: str
    config_or_script: str
    model: str
    prepared_at: float
    status: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _redact_text(value: str) -> str:
    result = str(value)
    for pattern in _SECRET_PATTERNS[:2]:
        result = pattern.sub(r"\1[REDACTED]", result)
    result = _SECRET_PATTERNS[2].sub("[REDACTED]", result)
    return result


def _redact_value(value):
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if str(key).casefold().replace("-", "_") in {
                "api_key", "apikey", "token", "secret", "password", "passphrase",
                "authorization", "client_secret",
            }:
                result[key] = "[REDACTED]"
            else:
                result[key] = _redact_value(item)
        return result
    return value


class TrainingDatasetManager:
    """Explicit-opt-in dataset copy with redaction and provenance metadata."""

    def __init__(self, root: str | Path, *, export_root: str | Path | None = None) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.export_root = Path(export_root).expanduser().resolve() if export_root else self.root
        self.export_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _validate_source(source: str | Path) -> Path:
        path = Path(source).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        if path.suffix.casefold() not in {".jsonl", ".json"}:
            raise ValueError("training dataset must be .jsonl or .json")
        if path.stat().st_size > 512 * 1024 * 1024:
            raise ValueError("training dataset exceeds 512 MiB safety limit")
        return path

    def preview(self, source: str | Path, *, rows: int = 3) -> list[object]:
        path = self._validate_source(source)
        if path.suffix.casefold() == ".jsonl":
            output = []
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    output.append(_redact_value(json.loads(line)))
                    if len(output) >= max(1, int(rows)):
                        break
            return output
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return [_redact_value(item) for item in payload[: max(1, int(rows))]]
        return [_redact_value(payload)]

    def preview_managed(self, source: str | Path, *, rows: int = 3) -> list[object]:
        """Preview only datasets already imported into the managed dataset directory."""
        candidate = Path(source).expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate
        try:
            managed = resolve_within(self.root, candidate, must_exist=True)
        except SecurityValidationError as exc:
            raise PermissionError("dataset preview must stay inside the managed dataset directory") from exc
        return self.preview(managed, rows=rows)

    def export(self, source: str | Path, destination: str | Path, *, approved: bool) -> Path:
        if not approved:
            raise PermissionError("dataset export requires explicit approval")
        source_path = self._validate_source(source)
        try:
            target = resolve_within(self.export_root, destination)
        except SecurityValidationError as exc:
            raise PermissionError("dataset export destination escapes the approved ModelLab root") from exc
        target.parent.mkdir(parents=True, exist_ok=True)
        if source_path.suffix.casefold() == ".jsonl":
            fd, temp_name = tempfile.mkstemp(prefix="dataset-", suffix=".tmp", dir=target.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as out:
                    with source_path.open("r", encoding="utf-8") as handle:
                        for number, line in enumerate(handle, start=1):
                            if not line.strip():
                                continue
                            try:
                                row = json.loads(line)
                            except json.JSONDecodeError as exc:
                                raise ValueError(f"invalid JSONL at line {number}") from exc
                            out.write(json.dumps(_redact_value(row), ensure_ascii=False) + "\n")
                    out.flush(); os.fsync(out.fileno())
                os.replace(temp_name, target)
            except Exception:
                try: os.unlink(temp_name)
                except OSError: pass
                raise
        else:
            payload = json.loads(source_path.read_text(encoding="utf-8"))
            target.write_text(json.dumps(_redact_value(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return target


class ModelLab:
    """On-demand Unsloth + Soup training control plane.

    Nothing heavy is imported or launched during normal voice runtime. Preparing a job requires
    explicit dataset approval; executing training requires a second explicit approval and a
    compatible NVIDIA host.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.jobs_dir = self.root / "jobs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.datasets = TrainingDatasetManager(self.root / "datasets", export_root=self.root)

    @staticmethod
    def _nvidia_gpus() -> tuple[GPUInfo, ...]:
        executable = shutil.which("nvidia-smi")
        if not executable:
            return ()
        try:
            result = subprocess.run(
                [executable, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
                **hidden_subprocess_kwargs(),
            )
        except (OSError, subprocess.TimeoutExpired):
            return ()
        if result.returncode != 0:
            return ()
        gpus = []
        for line in result.stdout.splitlines():
            if "," not in line:
                continue
            name, memory = line.rsplit(",", 1)
            try:
                gpus.append(GPUInfo(name.strip(), int(float(memory.strip()))))
            except ValueError:
                continue
        return tuple(gpus)

    def status(self) -> ModelLabStatus:
        unsloth_installed = importlib.util.find_spec("unsloth") is not None
        soup_executable = shutil.which("soup")
        gpus = self._nvidia_gpus()
        compatible = bool(gpus)
        reason = "NVIDIA GPU detected" if compatible else "No NVIDIA CUDA GPU detected; prepare jobs locally and run them on a compatible host"
        return ModelLabStatus(
            unsloth_installed,
            bool(soup_executable or importlib.util.find_spec("soup_cli") is not None),
            soup_executable,
            gpus,
            compatible,
            reason,
        )

    def _job_dir(self, backend: str) -> tuple[str, Path]:
        job_id = f"{int(time.time())}-{backend}-{uuid.uuid4().hex[:8]}"
        directory = resolve_within(self.jobs_dir, self.jobs_dir / job_id)
        directory.mkdir(parents=True, exist_ok=False)
        return job_id, directory

    def _load_job(self, job_id: str) -> tuple[Path, dict[str, Any]]:
        value = str(job_id).strip()
        if not _JOB_ID.fullmatch(value):
            raise KeyError("invalid ModelLab job id")
        try:
            directory = resolve_within(self.jobs_dir, self.jobs_dir / value, must_exist=True)
        except (SecurityValidationError, FileNotFoundError) as exc:
            raise KeyError(f"unknown ModelLab job: {value}") from exc
        job_path = directory / "job.json"
        if not job_path.is_file():
            raise KeyError(f"unknown ModelLab job: {value}")
        try:
            raw = json.loads(job_path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("ModelLab job metadata is invalid") from exc
        if not isinstance(raw, dict) or raw.get("job_id") != value:
            raise ValueError("ModelLab job metadata identity mismatch")
        backend = str(raw.get("backend") or "").casefold()
        if backend not in {"unsloth", "soup"}:
            raise ValueError("ModelLab job backend is invalid")
        expected_config = directory / ("train_unsloth.py" if backend == "unsloth" else "soup.yaml")
        expected_dataset = directory / "dataset.jsonl"
        if not expected_config.is_file() or not expected_dataset.is_file():
            raise ValueError("ModelLab job artifacts are incomplete")
        job = dict(raw)
        job["backend"] = backend
        job["job_dir"] = str(directory)
        job["dataset"] = str(expected_dataset)
        job["config_or_script"] = str(expected_config)
        return directory, job

    def prepare(self, backend: str, source: str | Path, model: str, *, approved_dataset: bool) -> ModelLabJob:
        normalized = str(backend).strip().casefold()
        if normalized not in {"unsloth", "soup"}:
            raise ValueError("backend must be unsloth or soup")
        model_name = str(model).strip()
        if not model_name or len(model_name) > 300:
            raise ValueError("model is required")
        job_id, directory = self._job_dir(normalized)
        dataset = self.datasets.export(source, directory / "dataset.jsonl", approved=approved_dataset)
        if normalized == "unsloth":
            entrypoint = directory / "train_unsloth.py"
            entrypoint.write_text(self._unsloth_script(model_name, dataset, directory / "output"), encoding="utf-8")
        else:
            entrypoint = directory / "soup.yaml"
            entrypoint.write_text(self._soup_config(model_name, dataset, directory / "output"), encoding="utf-8")
        job = ModelLabJob(job_id, normalized, str(directory), str(dataset), str(entrypoint), model_name, time.time(), "PREPARED")
        (directory / "job.json").write_text(json.dumps(job.as_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return job

    @staticmethod
    def _unsloth_script(model: str, dataset: Path, output: Path) -> str:
        return f'''from datasets import load_dataset\nfrom trl import SFTConfig, SFTTrainer\nfrom unsloth import FastLanguageModel, is_bfloat16_supported\n\nMODEL = {model!r}\nDATASET = {str(dataset)!r}\nOUTPUT = {str(output)!r}\nMAX_SEQ = 2048\n\nmodel, tokenizer = FastLanguageModel.from_pretrained(\n    model_name=MODEL, max_seq_length=MAX_SEQ, dtype=None, load_in_4bit=True\n)\nmodel = FastLanguageModel.get_peft_model(\n    model, r=16, lora_alpha=16, lora_dropout=0, bias="none",\n    target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],\n    use_gradient_checkpointing="unsloth", random_state=3407,\n)\ndataset = load_dataset("json", data_files=DATASET, split="train")\n\ndef to_text(row):\n    if isinstance(row.get("text"), str) and row["text"].strip():\n        return {{"text": row["text"]}}\n    instruction = str(row.get("instruction", ""))\n    user_input = str(row.get("input", ""))\n    output = str(row.get("output", row.get("response", "")))\n    return {{"text": f"Instruction: {{instruction}}\\nInput: {{user_input}}\\nResponse: {{output}}"}}\n\ndataset = dataset.map(to_text)\nargs = SFTConfig(\n    output_dir=OUTPUT, dataset_text_field="text", max_length=MAX_SEQ,\n    per_device_train_batch_size=1, gradient_accumulation_steps=4,\n    num_train_epochs=1, learning_rate=2e-4, logging_steps=1,\n    fp16=not is_bfloat16_supported(), bf16=is_bfloat16_supported(), report_to="none",\n)\ntrainer = SFTTrainer(model=model, processing_class=tokenizer, train_dataset=dataset, args=args)\ntrainer.train()\nmodel.save_pretrained(OUTPUT)\ntokenizer.save_pretrained(OUTPUT)\n'''

    @staticmethod
    def _soup_config(model: str, dataset: Path, output: Path) -> str:
        safe_model = json.dumps(model, ensure_ascii=False)
        safe_dataset = json.dumps(str(dataset), ensure_ascii=False)
        safe_output = json.dumps(str(output), ensure_ascii=False)
        return (
            "# Generated by ai aharon ModelLab for Soup\n"
            f"base: {safe_model}\n"
            "task: sft\n\n"
            "data:\n"
            f"  train: {safe_dataset}\n"
            "  format: auto\n"
            "  val_split: 0.1\n\n"
            "training:\n"
            "  epochs: 1\n"
            "  lr: 2e-5\n"
            "  batch_size: auto\n"
            "  lora:\n"
            "    r: 16\n"
            "    alpha: 16\n"
            "    target_modules: auto\n"
            "  quantization: nf4\n"
            f"output: {safe_output}\n"
        )

    def run(self, job_id: str, *, approved_run: bool, execute: bool = False) -> dict[str, Any]:
        directory, job = self._load_job(job_id)
        backend = str(job["backend"])
        status = self.status()
        if not execute:
            return {"status": "READY" if status.training_compatible else "INCOMPATIBLE_LOCAL_HOST", "job": job, "environment": status.as_dict()}
        if not approved_run:
            raise PermissionError("training execution requires explicit run approval")
        if not status.training_compatible:
            raise RuntimeError(status.reason)

        if backend == "unsloth":
            if not status.unsloth_installed:
                raise RuntimeError("Unsloth is not installed on this training host")
            command = [sys.executable, str(directory / "train_unsloth.py")]
        elif backend == "soup":
            executable = status.soup_executable or shutil.which("soup")
            if not executable:
                raise RuntimeError("Soup CLI is not installed on this training host")
            command = [executable, "train", "--config", str(directory / "soup.yaml"), "--yes"]
        else:
            raise ValueError(f"unsupported backend in job: {backend}")

        result = subprocess.run(
            command,
            cwd=directory,
            capture_output=True,
            text=True,
            timeout=24 * 60 * 60,
            check=False,
            **hidden_subprocess_kwargs(),
        )
        report = {
            "status": "COMPLETED" if result.returncode == 0 else "FAILED",
            "returncode": result.returncode,
            "stdout_tail": _redact_text(result.stdout[-4000:]),
            "stderr_tail": _redact_text(result.stderr[-4000:]),
            "job": job,
        }
        (directory / "run-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return report

    def soup_dry_run(self, job_id: str) -> dict[str, Any]:
        directory, job = self._load_job(job_id)
        if job.get("backend") != "soup":
            raise ValueError("soup_dry_run requires a Soup job")
        executable = shutil.which("soup")
        if not executable:
            return {"status": "CODE_READY", "detail": "Soup CLI not installed", "job": job}
        result = subprocess.run(
            [executable, "train", "--config", str(directory / "soup.yaml"), "--dry-run", "--yes"],
            cwd=directory,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            **hidden_subprocess_kwargs(),
        )
        return {
            "status": "OK" if result.returncode == 0 else "FAIL",
            "returncode": result.returncode,
            "stdout_tail": _redact_text(result.stdout[-3000:]),
            "stderr_tail": _redact_text(result.stderr[-3000:]),
        }


def build_model_lab_tools(lab: ModelLab) -> list[FunctionTool]:
    def status(_args: Mapping[str, object]):
        return lab.status().as_dict()

    def preview(args: Mapping[str, object]):
        return lab.datasets.preview_managed(str(args.get("dataset") or ""), rows=int(args.get("rows") or 3))

    return [
        FunctionTool(
            "model_lab_status",
            "Check Unsloth/Soup installation and training-host compatibility",
            {"type": "object", "properties": {}},
            status,
            risk="read_only",
        ),
        FunctionTool(
            "training_dataset_preview",
            "Preview a locally redacted training dataset without starting training",
            {
                "type": "object",
                "properties": {"dataset": {"type": "string"}, "rows": {"type": "integer"}},
                "required": ["dataset"],
            },
            preview,
            risk="read_only",
        ),
    ]
