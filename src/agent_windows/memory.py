from __future__ import annotations

import json
import math
import re
from typing import Any, Mapping, Sequence
import sqlite3
import threading
import time
from pathlib import Path

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def _tokens(text: str, *, limit: int = 12) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for token in _TOKEN_RE.findall(text.casefold()):
        if token in seen or (len(token) <= 2 and not token.isdigit()):
            continue
        seen.add(token)
        result.append(token)
        if len(result) >= limit:
            break
    return result


def _importance(metadata: Mapping[str, Any] | None) -> float:
    if not metadata:
        return 0.5
    try:
        value = float(metadata.get("importance", 0.5))
    except (TypeError, ValueError):
        value = 0.5
    return max(0.0, min(1.0, value))


class InMemoryStore:
    """Deterministic in-process memory with relevance and recency ordering."""

    def __init__(self) -> None:
        self._items: list[tuple[str, float, Mapping[str, Any]]] = []

    def remember(self, text: str, *, metadata: Mapping[str, Any] | None = None) -> None:
        clean = text.strip()
        if clean and all(clean != item[0] for item in self._items):
            self._items.append((clean, time.time(), dict(metadata or {})))

    def search(self, query: str, *, limit: int = 5) -> Sequence[str]:
        terms = set(_tokens(query))
        if not terms or limit <= 0:
            return []
        now = time.time()
        scored = []
        for index, (item, created, metadata) in enumerate(self._items):
            folded = item.casefold()
            hits = sum(term in folded for term in terms)
            if not hits:
                continue
            age_days = max(0.0, now - created) / 86400.0
            score = hits * 2.0 + (hits / len(terms)) * 2.0
            score += _importance(metadata) * 1.25
            score += 0.5 / (1.0 + age_days / 30.0)
            scored.append((score, index, item))
        scored.sort(key=lambda row: (-row[0], -row[1]))
        return [item for _, _, item in scored[:limit]]


class SQLiteMemoryStore:
    """Durable ranked memory with lightweight schema migration and bounded storage."""

    def __init__(self, path: str | Path, *, max_items: int = 5000) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_items = max_items
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None
        try:
            self._connection = self._connect()
            self._initialize()
        except BaseException:
            self.close()
            raise

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5, check_same_thread=False)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            return connection
        except BaseException:
            connection.close()
            raise

    def _initialize(self) -> None:
        with self._lock:
            with self._database() as db:
                result = db.execute("PRAGMA quick_check").fetchone()
                if result is None or result[0] != "ok":
                    raise sqlite3.DatabaseError("memory database integrity check failed")
                db.execute(
                    "CREATE TABLE IF NOT EXISTS memories("
                    "id INTEGER PRIMARY KEY, text TEXT UNIQUE NOT NULL, created REAL NOT NULL, metadata TEXT)"
                )
                columns = {row[1] for row in db.execute("PRAGMA table_info(memories)")}
                migrations = {
                    "kind": "ALTER TABLE memories ADD COLUMN kind TEXT NOT NULL DEFAULT 'turn'",
                    "importance": "ALTER TABLE memories ADD COLUMN importance REAL NOT NULL DEFAULT 0.5",
                    "last_accessed": "ALTER TABLE memories ADD COLUMN last_accessed REAL",
                    "access_count": "ALTER TABLE memories ADD COLUMN access_count INTEGER NOT NULL DEFAULT 0",
                }
                for column, statement in migrations.items():
                    if column not in columns:
                        db.execute(statement)
                db.execute("CREATE INDEX IF NOT EXISTS memories_created ON memories(created)")
                db.execute(
                    "CREATE INDEX IF NOT EXISTS memories_importance ON memories(importance DESC, created DESC)"
                )

    def remember(self, text: str, *, metadata: Mapping[str, Any] | None = None) -> None:
        clean = text.strip()
        if not clean:
            return
        meta = dict(metadata or {})
        importance = _importance(meta)
        kind = str(meta.get("kind", "turn") or "turn")[:40]
        now = time.time()
        with self._lock:
            with self._database() as db:
                db.execute(
                    "INSERT INTO memories(text,created,metadata,kind,importance,last_accessed,access_count) "
                    "VALUES(?,?,?,?,?,?,0) "
                    "ON CONFLICT(text) DO UPDATE SET "
                    "metadata=excluded.metadata, kind=excluded.kind, "
                    "importance=MAX(memories.importance, excluded.importance)",
                    (clean, now, json.dumps(meta, ensure_ascii=False), kind, importance, None),
                )
                db.execute(
                    "DELETE FROM memories WHERE id IN ("
                    "SELECT id FROM memories ORDER BY importance DESC, created DESC "
                    "LIMIT -1 OFFSET ?)",
                    (self.max_items,),
                )

    def search(self, query: str, *, limit: int = 5) -> Sequence[str]:
        terms = _tokens(query)
        if not terms or limit <= 0:
            return []
        # Keep the SQL statement static. With at most 5,000 local memories, ranking the
        # bounded store in Python is inexpensive and avoids constructing SQL from query shape.
        with self._lock:
            with self._database() as db:
                rows = db.execute(
                    "SELECT id,text,created,metadata,kind,importance,last_accessed,access_count "
                    "FROM memories ORDER BY importance DESC, created DESC LIMIT ?",
                    (self.max_items,),
                ).fetchall()
                ranked = self._rank(rows, query, terms)
                selected = ranked[:limit]
                if selected:
                    now = time.time()
                    db.executemany(
                        "UPDATE memories SET last_accessed=?, access_count=access_count+1 WHERE id=?",
                        [(now, row[1]) for row in selected],
                    )
        return [row[2] for row in selected]

    @staticmethod
    def _rank(rows, query: str, terms: list[str]):
        now = time.time()
        query_folded = query.casefold().strip()
        scored = []
        for row in rows:
            memory_id, text, created, _metadata, kind, importance, _last_accessed, access_count = row
            folded = text.casefold()
            hits = sum(term in folded for term in terms)
            if hits <= 0:
                continue
            coverage = hits / max(1, len(terms))
            exact = 1.0 if len(query_folded) >= 5 and query_folded in folded else 0.0
            age_days = max(0.0, now - float(created)) / 86400.0
            recency = 1.0 / (1.0 + age_days / 45.0)
            access = min(1.0, math.log1p(max(0, int(access_count))) / 4.0)
            kind_boost = {
                "profile": 1.2,
                "preference": 1.15,
                "instruction": 1.1,
                "goal": 0.9,
                "turn": 0.0,
            }.get(str(kind), 0.2)
            score = (
                hits * 2.0
                + coverage * 3.0
                + exact * 2.5
                + float(importance) * 2.0
                + recency * 0.8
                + access * 0.5
                + kind_boost
            )
            scored.append((score, memory_id, text))
        scored.sort(key=lambda item: (-item[0], -item[1]))
        return scored

    def delete(self, memory_id: int | None = None) -> int:
        with self._lock:
            with self._database() as db:
                cursor = db.execute(
                    "DELETE FROM memories" if memory_id is None else "DELETE FROM memories WHERE id=?",
                    () if memory_id is None else (memory_id,),
                )
                return cursor.rowcount

    def _database(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("memory store is closed")
        return self._connection

    def close(self) -> None:
        """Close the database handle. Repeated calls are safe."""
        with self._lock:
            connection, self._connection = self._connection, None
            if connection is not None:
                connection.close()

    def __enter__(self) -> "SQLiteMemoryStore":
        self._database()
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()
