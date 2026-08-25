from __future__ import annotations

from dataclasses import dataclass
from itertools import chain
from typing import Iterable, Protocol

from .chunking import AudioChunk, UploadSession


@dataclass(frozen=True)
class ChunkAck:
    session_id: str
    sequence: int
    accepted: bool = True
    duplicate: bool = False


class AudioTransport(Protocol):
    """Shared boundary for direct-provider and PHP-relay transports."""

    def open(self, session_id: str, metadata: dict) -> None: ...
    def send_chunk(self, chunk: AudioChunk) -> ChunkAck: ...
    def finish(self, session_id: str) -> dict: ...


class UploadInterrupted(RuntimeError):
    def __init__(self, message: str, session: UploadSession) -> None:
        super().__init__(message)
        self.session = session


class ResilientUploader:
    def __init__(self, transport: AudioTransport, *, max_attempts: int = 3) -> None:
        self.transport = transport
        self.max_attempts = max(1, max_attempts)

    def upload(self, chunks: Iterable[AudioChunk], *, metadata: dict, session: UploadSession | None = None) -> tuple[dict, UploadSession]:
        iterator = iter(chunks)
        try:
            first = next(iterator)
        except StopIteration:
            raise ValueError("upload requires at least one chunk")
        state = session or UploadSession(first.session_id)
        if state.session_id != first.session_id:
            raise ValueError("session ID mismatch")
        self.transport.open(state.session_id, metadata)
        for chunk in chain((first,), iterator):
            if chunk.session_id != state.session_id:
                raise ValueError("mixed session IDs")
            if not state.needs(chunk.sequence):
                continue
            last_error = None
            for _ in range(self.max_attempts):
                try:
                    ack = self.transport.send_chunk(chunk)
                    if ack.session_id != state.session_id or ack.sequence != chunk.sequence:
                        raise ValueError("invalid acknowledgement")
                    if ack.accepted or ack.duplicate:
                        state.acknowledge(chunk.sequence)
                        last_error = None
                        break
                except (TimeoutError, ConnectionError) as exc:
                    last_error = exc
            if last_error is not None or state.needs(chunk.sequence):
                raise UploadInterrupted(f"chunk {chunk.sequence} was not acknowledged", state)
        return self.transport.finish(state.session_id), state
