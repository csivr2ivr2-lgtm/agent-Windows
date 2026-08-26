from __future__ import annotations

from .agent_loop import AgentLoop
from .contracts import MemoryStore, Message
from .optimizer import RequestOptimizer
from .policy import PolicyEngine
from .router import LLMRouter
from .tools import ToolRegistry


DEFAULT_SYSTEM_PROMPT = (
    "אתה העוזר האישי של אהרן. ענה תמיד בעברית, קצר וברור, אלא אם המשתמש ביקש שפה אחרת. "
    "אל תנחש שעה, תאריך או מידע מערכת. כאשר הבקשה תלויה במידע כזה, השתמש בכלי המערכת "
    "המתאים ורק אחר כך ענה."
)


class AgentOrchestrator:
    def __init__(
        self,
        router: LLMRouter,
        memory: MemoryStore,
        tools: ToolRegistry,
        optimizer: RequestOptimizer | None = None,
        policy_provider=None,
        policy_engine: PolicyEngine | None = None,
        confirmation_provider=None,
        tool_planner=None,
        plan_reviewer=None,
        skill_provider=None,
        goal_provider=None,
    ) -> None:
        self.router = router
        self.memory = memory
        self.tools = tools
        self.optimizer = optimizer or RequestOptimizer()
        self.policy_provider = policy_provider or (lambda: {"context_chars": 12000, "tools": 20})
        self.loop = AgentLoop(
            router,
            memory,
            tools,
            system_prompt=DEFAULT_SYSTEM_PROMPT,
            optimizer=self.optimizer,
            policy_provider=self.policy_provider,
            policy_engine=policy_engine,
            confirmation_provider=confirmation_provider,
            tool_planner=tool_planner,
            plan_reviewer=plan_reviewer,
            skill_provider=skill_provider,
            goal_provider=goal_provider,
        )

    @staticmethod
    def _ensure_system_prompt(messages: list[Message]) -> list[Message]:
        if messages and messages[0].role == "system" and DEFAULT_SYSTEM_PROMPT in messages[0].content:
            return messages
        return [Message("system", DEFAULT_SYSTEM_PROMPT), *messages]

    def handle_text(self, user_text: str) -> str:
        return self.loop.run(user_text).text
