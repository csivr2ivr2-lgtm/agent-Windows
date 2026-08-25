from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterator

from .chunking import AudioChunk


class OfflineAudioSpool:
    """Disk-backed encoded chunk spool; filenames never use client-supplied names."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _session_dir(self, session_id: str) -> Path:
        safe_id = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
        return self.root / safe_id

    def put(self, chunk: AudioChunk) -> None:
        directory = self._session_dir(chunk.session_id)
        directory.mkdir(parents=True, exist_ok=True)
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

    def iter_session(self, session_id: str) -> Iterator[AudioChunk]:
        directory = self._session_dir(session_id)
        if not directory.exists():
            return
        for metadata_path in sorted(directory.glob("*.json")):
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            payload = metadata_path.with_suffix(".audio").read_bytes()
            checksum = hashlib.sha256(payload).hexdigest()
            if checksum != metadata["checksum"]:
                raise ValueError(f"offline audio chunk checksum mismatch: {metadata['sequence']}")
            yield AudioChunk(
                metadata["session_id"], metadata["sequence"], metadata["timestamp_ms"],
                payload, checksum, metadata["final"],
            )
