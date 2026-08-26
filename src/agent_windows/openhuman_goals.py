from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping

from .windows_tools import FunctionTool


@dataclass(frozen=True)
class ThreadGoal:
    goal_id: str
    objective: str
    status: str
    max_steps: int
    max_tool_calls: int
    created_at: float
    updated_at: float


class OpenHumanGoalStore:
    """OpenHuman-inspired durable thread-goal completion contract.

    A goal can constrain the existing AgentLoop but never bypass its policy or budgets. The
    persistence format is intentionally tiny JSON so the Windows service stays lightweight.
    """

    VALID_STATUS = {"active", "complete", "paused"}

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _load(self) -> ThreadGoal | None:
        if not self.path.is_file():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return None
            return ThreadGoal(
                str(data["goal_id"]),
                str(data["objective"]),
                str(data["status"]),
                max(1, int(data["max_steps"])),
                max(0, int(data["max_tool_calls"])),
                float(data["created_at"]),
                float(data["updated_at"]),
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def _write(self, goal: ThreadGoal) -> None:
        fd, temp_name = tempfile.mkstemp(prefix="goal-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(asdict(goal), handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise

    def get(self) -> ThreadGoal | None:
        with self._lock:
            return self._load()

    def set(self, objective: str, *, max_steps: int = 8, max_tool_calls: int = 12) -> ThreadGoal:
        text = " ".join(str(objective).split())
        if not text:
            raise ValueError("goal objective is required")
        if len(text) > 2000:
            raise ValueError("goal objective exceeds 2000 characters")
        now = time.time()
        goal = ThreadGoal(
            uuid.uuid4().hex[:16],
            text,
            "active",
            max(1, min(64, int(max_steps))),
            max(0, min(128, int(max_tool_calls))),
            now,
            now,
        )
        with self._lock:
            self._write(goal)
        return goal

    def transition(self, status: str) -> ThreadGoal:
        normalized = str(status).strip().casefold()
        if normalized not in self.VALID_STATUS:
            raise ValueError(f"invalid goal status: {status}")
        with self._lock:
            current = self._load()
            if current is None:
                raise KeyError("no thread goal")
            updated = ThreadGoal(
                current.goal_id,
                current.objective,
                normalized,
                current.max_steps,
                current.max_tool_calls,
                current.created_at,
                time.time(),
            )
            self._write(updated)
            return updated

    def context(self) -> str:
        goal = self.get()
        if goal is None or goal.status != "active":
            return ""
        return (
            "[active_goal]\n"
            f"id: {goal.goal_id}\n"
            f"objective: {goal.objective}\n"
            f"max_steps: {goal.max_steps}\n"
            f"max_tool_calls: {goal.max_tool_calls}\n"
            "Completion rule: do not continue autonomously once the objective is satisfied."
        )

    def constrain(self, max_steps: int, max_tool_calls: int) -> tuple[int, int]:
        goal = self.get()
        if goal is None or goal.status != "active":
            return max_steps, max_tool_calls
        return min(max_steps, goal.max_steps), min(max_tool_calls, goal.max_tool_calls)


def build_openhuman_goal_tools(store: OpenHumanGoalStore) -> list[FunctionTool]:
    def get_goal(_args: Mapping[str, object]):
        goal = store.get()
        return asdict(goal) if goal else None

    def set_goal(args: Mapping[str, object]):
        return asdict(store.set(
            str(args.get("objective") or ""),
            max_steps=int(args.get("max_steps") or 8),
            max_tool_calls=int(args.get("max_tool_calls") or 12),
        ))

    return [
        FunctionTool(
            "goal_get",
            "Read the current OpenHuman-style thread goal",
            {"type": "object", "properties": {}},
            get_goal,
            risk="read_only",
        ),
        FunctionTool(
            "goal_set",
            "Create or replace the local thread goal completion contract",
            {
                "type": "object",
                "properties": {
                    "objective": {"type": "string"},
                    "max_steps": {"type": "integer"},
                    "max_tool_calls": {"type": "integer"},
                },
                "required": ["objective"],
            },
            set_goal,
            risk="low",
        ),
        FunctionTool(
            "goal_complete",
            "Mark the current thread goal complete",
            {"type": "object", "properties": {}},
            lambda _args: asdict(store.transition("complete")),
            risk="low",
        ),
        FunctionTool(
            "goal_pause",
            "Pause the current thread goal",
            {"type": "object", "properties": {}},
            lambda _args: asdict(store.transition("paused")),
            risk="low",
        ),
    ]
