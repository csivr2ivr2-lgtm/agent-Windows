from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from typing import BinaryIO, Iterator


@dataclass(frozen=True)
class AudioChunk:
    session_id: str
    sequence: int
    timestamp_ms: int
    payload: bytes
    checksum: str
    final: bool = False


class AudioChunker:
    """Reads incrementally; it never needs the complete recording in memory."""

    def __init__(self, chunk_bytes: int, *, session_id: str | None = None, chunk_duration_ms: int = 0) -> None:
        if chunk_bytes <= 0:
            raise ValueError("chunk_bytes must be positive")
        self.chunk_bytes = chunk_bytes
        self.session_id = session_id or uuid.uuid4().hex
        self.chunk_duration_ms = max(0, chunk_duration_ms)

    def iter_stream(self, stream: BinaryIO, *, started_ms: int | None = None) -> Iterator[AudioChunk]:
        started = int(time.time() * 1000) if started_ms is None else started_ms
        sequence = 0
        pending = stream.read(self.chunk_bytes)
        while pending:
            following = stream.read(self.chunk_bytes)
            yield AudioChunk(
                self.session_id, sequence, started + sequence * self.chunk_duration_ms, pending,
                hashlib.sha256(pending).hexdigest(), final=not following,
            )
            sequence += 1
            pending = following


@dataclass
class UploadSession:
    session_id: str
    acknowledged: set[int] = field(default_factory=set)

    def acknowledge(self, sequence: int) -> bool:
        duplicate = sequence in self.acknowledged
        self.acknowledged.add(sequence)
        return not duplicate

    def needs(self, sequence: int) -> bool:
        return sequence not in self.acknowledged

    @property
    def resume_from(self) -> int:
        sequence = 0
        while sequence in self.acknowledged:
            sequence += 1
        return sequence
