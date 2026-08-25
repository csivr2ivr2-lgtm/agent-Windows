from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterator

from .chunking import AudioChunk


class OfflineAudioSpool:
    """Disk-backed encoded chunk spool; filenames never use client-supplied names."""

    def __init__(self, root: str | Path, *, max_bytes: int = 100 * 1024 * 1024) -> None:
        self.root = Path(root)
        self.max_bytes = max_bytes

    def _session_dir(self, session_id: str) -> Path:
        safe_id = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
        return self.root / safe_id

    def put(self, chunk: AudioChunk, *, session_metadata: dict | None = None) -> None:
        directory = self._session_dir(chunk.session_id)
        directory.mkdir(parents=True, exist_ok=True)
        if session_metadata is not None:
            (directory / "session.json").write_text(json.dumps({"session_id":chunk.session_id,**session_metadata},separators=(",",":")),encoding="utf-8")
        stem = f"{chunk.sequence:012d}"
        payload_path = directory / f"{stem}.audio"
        metadata_path = directory / f"{stem}.json"
        payload_path.write_bytes(chunk.payload)
        metadata_path.write_text(json.dumps({
            "session_id": chunk.session_id,
            "sequence": chunk.sequence,
            "timestamp_ms": chunk.timestamp_ms,
            "checksum": chunk.checksum,
            "final": chunk.final,
        }, separators=(",", ":")), encoding="utf-8")
        self._enforce_limit()

    def iter_session(self, session_id: str) -> Iterator[AudioChunk]:
        directory = self._session_dir(session_id)
        if not directory.exists():
            return
        for metadata_path in sorted(directory.glob("*.json")):
            if metadata_path.name == "session.json": continue
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            payload = metadata_path.with_suffix(".audio").read_bytes()
            checksum = hashlib.sha256(payload).hexdigest()
            if checksum != metadata["checksum"]:
                raise ValueError(f"offline audio chunk checksum mismatch: {metadata['sequence']}")
            yield AudioChunk(
                metadata["session_id"], metadata["sequence"], metadata["timestamp_ms"],
                payload, checksum, metadata["final"],
            )

    def sessions(self) -> list[str]:
        found = []
        if not self.root.exists(): return found
        for metadata in self.root.glob("*/*.json"):
            if metadata.name == "session.json": continue
            try:
                session_id = json.loads(metadata.read_text(encoding="utf-8"))["session_id"]
                if session_id not in found: found.append(session_id)
            except (OSError, KeyError, ValueError): continue
        return found

    def session_metadata(self, session_id: str) -> dict:
        path=self._session_dir(session_id)/"session.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def delete_session(self, session_id: str) -> None:
        import shutil
        directory = self._session_dir(session_id)
        if directory.exists(): shutil.rmtree(directory)

    def _enforce_limit(self) -> None:
        if not self.root.exists(): return
        files = [p for p in self.root.glob("**/*") if p.is_file()]
        total = sum(p.stat().st_size for p in files)
        if total <= self.max_bytes: return
        for directory in sorted((p for p in self.root.iterdir() if p.is_dir()), key=lambda p:p.stat().st_mtime):
            import shutil
            size=sum(p.stat().st_size for p in directory.glob("**/*") if p.is_file()); shutil.rmtree(directory); total-=size
            if total <= self.max_bytes: break
