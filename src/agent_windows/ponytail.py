from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Mapping, Sequence

from .contracts import ToolCall
from .windows_tools import FunctionTool


_DEPENDENCY_RE = re.compile(
    r"\b(?:pip\s+install|npm\s+install|pnpm\s+add|yarn\s+add|new dependency|new package|framework)\b",
    re.IGNORECASE,
)
_NEW_ABSTRACTION_RE = re.compile(
    r"\b(?:new service|new class|new adapter|new layer|new framework|microservice|rewrite)\b",
    re.IGNORECASE,
)
_SAFETY_RE = re.compile(
    r"\b(?:delete|credential|password|token|payment|registry|admin|shell|powershell|security settings)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PlanReview:
    complexity: int
    threshold_crossed: bool
    rung: str
    recommendations: tuple[str, ...]
    safety_flags: tuple[str, ...]

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ToolBatchReview:
    calls: tuple[ToolCall, ...]
    removed_duplicates: int
    threshold_crossed: bool
    note: str = ""


class PonytailReviewer:
    """Python-native implementation of Ponytail's documented minimal-solution ladder.

    This is deliberately deterministic: it never removes validation, security, confirmation,
    or accessibility requirements merely to make a plan shorter.
    """

    def __init__(self, *, complexity_threshold: int = 4) -> None:
        self.complexity_threshold = max(1, int(complexity_threshold))

    def review_plan(self, plan: str) -> PlanReview:
        text = " ".join(str(plan).split())
        if not text:
            return PlanReview(0, False, "skip", ("אין מה לממש.",), ())

        complexity = 1
        recommendations: list[str] = []
        safety_flags: list[str] = []
        separators = text.count(";") + text.count("→") + text.lower().count(" then ")
        complexity += min(4, separators)
        if _DEPENDENCY_RE.search(text):
            complexity += 2
            recommendations.append("בדוק קודם stdlib, API קיים או dependency שכבר מותקן לפני הוספת תלות חדשה.")
        if _NEW_ABSTRACTION_RE.search(text):
            complexity += 2
            recommendations.append("העדף reuse של הזרימה הקיימת לפני יצירת שכבה/שירות/adapter חדש.")
        if len(text) > 800:
            complexity += 1
            recommendations.append("פצל את התוכנית לצעדים אטומיים עם תנאי השלמה ברור.")
        if _SAFETY_RE.search(text):
            safety_flags.append("הפעולה נוגעת בגבול אמון; אין לקצר validation, confirmation או rollback.")

        if complexity <= 1:
            rung = "one-line/minimum"
        elif not _DEPENDENCY_RE.search(text):
            rung = "stdlib/native/existing"
        else:
            rung = "minimum-that-works"
        crossed = complexity >= self.complexity_threshold
        if crossed and not recommendations:
            recommendations.append("בדוק אם אפשר לבצע את אותה מטרה בפחות צעדים או עם יכולת שכבר קיימת במערכת.")
        return PlanReview(
            complexity,
            crossed,
            rung,
            tuple(recommendations),
            tuple(safety_flags),
        )

    def review_tool_calls(self, calls: Sequence[ToolCall]) -> ToolBatchReview:
        seen: set[str] = set()
        kept: list[ToolCall] = []
        removed = 0
        for call in calls:
            key = json.dumps(
                {"name": call.name, "arguments": call.arguments},
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
            if key in seen:
                removed += 1
                continue
            seen.add(key)
            kept.append(call)
        crossed = len(kept) >= self.complexity_threshold
        note = ""
        if removed:
            note = f"Ponytail removed {removed} exact duplicate tool call(s)."
        elif crossed:
            note = "Ponytail complexity threshold crossed; keep the tool batch minimal and safe."
        return ToolBatchReview(tuple(kept), removed, crossed, note)


def build_ponytail_tools(reviewer: PonytailReviewer) -> list[FunctionTool]:
    def review(args: Mapping[str, object]):
        result = reviewer.review_plan(str(args.get("plan") or ""))
        return result.as_dict()

    return [
        FunctionTool(
            "review_plan",
            "Review a technical plan using the Ponytail YAGNI/reuse/stdlib/native/minimum ladder",
            {
                "type": "object",
                "properties": {"plan": {"type": "string"}},
                "required": ["plan"],
            },
            review,
            risk="read_only",
        )
    ]
