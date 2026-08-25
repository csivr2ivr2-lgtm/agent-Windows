from __future__ import annotations

import json
from typing import Any, Mapping, Sequence
import sqlite3
import time
from pathlib import Path


class InMemoryStore:
    """Deterministic MVP backend; replace through MemoryStore, not call sites."""

    def __init__(self) -> None:
        self._items: list[str] = []

    def remember(self, text: str, *, metadata: Mapping[str, Any] | None = None) -> None:
        clean = text.strip()
        if clean and clean not in self._items:
            self._items.append(clean)

    def search(self, query: str, *, limit: int = 5) -> Sequence[str]:
        terms = {word.casefold() for word in query.split() if len(word) > 2}
        scored = []
        for index, item in enumerate(self._items):
            score = sum(term in item.casefold() for term in terms)
            if score:
                scored.append((score, index, item))
        scored.sort(key=lambda row: (-row[0], -row[1]))
        return [item for _, _, item in scored[:limit]]


class SQLiteMemoryStore:
    """Bounded persistent memory using SQLite FTS-free queries for portability."""

    def __init__(self, path: str | Path, *, max_items: int = 5000) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_items = max_items
        self._connection: sqlite3.Connection | None = None
        try:
            self._connection = self._connect()
            self._initialize()
        except BaseException:
            self.close()
            raise

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            return connection
        except BaseException:
            connection.close()
            raise

    def _initialize(self) -> None:
        with self._database() as db:
            result = db.execute("PRAGMA quick_check").fetchone()
            if result is None or result[0] != "ok":
                raise sqlite3.DatabaseError("memory database integrity check failed")
            db.execute("CREATE TABLE IF NOT EXISTS memories(id INTEGER PRIMARY KEY, text TEXT UNIQUE NOT NULL, created REAL NOT NULL, metadata TEXT)")
            db.execute("CREATE INDEX IF NOT EXISTS memories_created ON memories(created)")

    def remember(self, text: str, *, metadata: Mapping[str, Any] | None = None) -> None:
        clean = text.strip()
        if not clean:
            return
        with self._database() as db:
            db.execute("INSERT OR IGNORE INTO memories(text,created,metadata) VALUES(?,?,?)",
                       (clean, time.time(), json.dumps(metadata or {}, ensure_ascii=False)))
            db.execute("DELETE FROM memories WHERE id IN (SELECT id FROM memories ORDER BY created DESC LIMIT -1 OFFSET ?)",
                       (self.max_items,))

    def search(self, query: str, *, limit: int = 5) -> Sequence[str]:
        terms = [word for word in query.split() if len(word) > 2][:6]
        if not terms:
            return []
        where = " OR ".join("text LIKE ? ESCAPE '\\'" for _ in terms)
        escaped = [term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") for term in terms]
        with self._database() as db:
            rows = db.execute(f"SELECT text FROM memories WHERE {where} ORDER BY created DESC LIMIT ?",
                              (*[f"%{term}%" for term in escaped], max(0, limit))).fetchall()
        return [row[0] for row in rows]

    def delete(self, memory_id: int | None = None) -> int:
        with self._database() as db:
            cursor = db.execute("DELETE FROM memories" if memory_id is None else "DELETE FROM memories WHERE id=?",
                                () if memory_id is None else (memory_id,))
            return cursor.rowcount

    def _database(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("memory store is closed")
        return self._connection

    def close(self) -> None:
        """Close the database handle. Repeated calls are safe."""
        connection, self._connection = self._connection, None
        if connection is not None:
            connection.close()

    def __enter__(self) -> "SQLiteMemoryStore":
        self._database()
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()
