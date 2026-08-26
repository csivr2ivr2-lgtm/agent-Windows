from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .windows_tools import FunctionTool


_SKILL_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_WORD = re.compile(r"[\w\-]{2,}", re.UNICODE)


@dataclass(frozen=True)
class SkillDocument:
    name: str
    content: str
    path: str
    score: int = 0


class HermesSkillStore:
    """Hermes-inspired durable skill library using the portable SKILL.md format.

    Skills are local text files, written atomically and never executed directly. The AgentLoop
    may retrieve their instructions as context; any tool action described by a skill still goes
    through the normal ToolRegistry and PolicyEngine.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _validate_name(name: str) -> str:
        value = str(name).strip().casefold()
        if not _SKILL_NAME.fullmatch(value):
            raise ValueError("skill name must match [a-z0-9][a-z0-9_-]{0,63}")
        return value

    def _path(self, name: str) -> Path:
        value = self._validate_name(name)
        target = (self.root / value / "SKILL.md").resolve()
        if self.root not in target.parents:
            raise ValueError("skill path escapes skill root")
        return target

    def list(self) -> list[str]:
        result = []
        for child in sorted(self.root.iterdir()) if self.root.exists() else ():
            if child.is_dir() and _SKILL_NAME.fullmatch(child.name) and (child / "SKILL.md").is_file():
                result.append(child.name)
        return result

    def read(self, name: str) -> SkillDocument:
        path = self._path(name)
        if not path.is_file():
            raise KeyError(f"unknown skill: {name}")
        return SkillDocument(path.parent.name, path.read_text(encoding="utf-8"), str(path))

    def create(self, name: str, content: str, *, replace: bool = False) -> SkillDocument:
        path = self._path(name)
        text = str(content).strip()
        if len(text) < 20:
            raise ValueError("SKILL.md content is too short")
        if len(text.encode("utf-8")) > 256 * 1024:
            raise ValueError("SKILL.md exceeds 256 KiB")
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and not replace:
            raise FileExistsError(f"skill already exists: {path.parent.name}")
        fd, temp_name = tempfile.mkstemp(prefix="SKILL-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
        return self.read(path.parent.name)

    def delete(self, name: str) -> bool:
        path = self._path(name)
        if not path.exists():
            return False
        path.unlink()
        try:
            path.parent.rmdir()
        except OSError:
            pass
        return True

    def search(self, query: str, *, limit: int = 3) -> list[SkillDocument]:
        terms = {word.casefold() for word in _WORD.findall(str(query))}
        if not terms:
            return []
        matches: list[SkillDocument] = []
        for name in self.list():
            doc = self.read(name)
            haystack = doc.content.casefold()
            score = sum(3 if term in name else haystack.count(term) for term in terms)
            if score:
                matches.append(SkillDocument(doc.name, doc.content, doc.path, score))
        matches.sort(key=lambda item: (-item.score, item.name))
        return matches[: max(1, int(limit))]

    def context(self, query: str, *, max_chars: int = 5000) -> str:
        pieces = []
        remaining = max(0, int(max_chars))
        for doc in self.search(query):
            block = f"[skill:{doc.name}]\n{doc.content}\n"
            if remaining <= 0:
                break
            block = block[:remaining]
            pieces.append(block)
            remaining -= len(block)
        return "\n".join(pieces)


def build_hermes_skill_tools(store: HermesSkillStore) -> list[FunctionTool]:
    return [
        FunctionTool(
            "skills_list",
            "List durable local Hermes-style skills",
            {"type": "object", "properties": {}},
            lambda _args: store.list(),
            risk="read_only",
        ),
        FunctionTool(
            "skills_read",
            "Read one local SKILL.md",
            {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
            lambda args: store.read(str(args.get("name") or "")).content,
            risk="read_only",
        ),
        FunctionTool(
            "skills_create",
            "Create or intentionally replace a local SKILL.md after user confirmation",
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "content": {"type": "string"},
                    "replace": {"type": "boolean"},
                },
                "required": ["name", "content"],
            },
            lambda args: store.create(
                str(args.get("name") or ""),
                str(args.get("content") or ""),
                replace=bool(args.get("replace", False)),
            ).path,
            risk="medium",
        ),
        FunctionTool(
            "skills_delete",
            "Delete a local skill after explicit confirmation",
            {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
            lambda args: store.delete(str(args.get("name") or "")),
            risk="high",
        ),
    ]
