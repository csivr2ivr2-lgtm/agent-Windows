from __future__ import annotations

from .contracts import MemoryStore, Message
from .router import LLMRouter
from .tools import ToolRegistry
from .optimizer import RequestOptimizer


class AgentOrchestrator:
    def __init__(self, router: LLMRouter, memory: MemoryStore, tools: ToolRegistry,
                 optimizer: RequestOptimizer | None = None, policy_provider=None) -> None:
        self.router = router
        self.memory = memory
        self.tools = tools
        self.optimizer = optimizer or RequestOptimizer()
        self.policy_provider = policy_provider or (lambda: {"context_chars": 12000, "tools": 20})

    def handle_text(self, user_text: str) -> str:
        context = self.memory.search(user_text)
        messages = []
        if context:
            messages.append(Message("system", "Relevant memory:\n" + "\n".join(context)))
        messages.append(Message("user", user_text))

        policy = self.policy_provider()
        messages, schemas = self.optimizer.optimize(messages, self.tools.schemas(),
                                                     max_chars=int(policy["context_chars"]), max_tools=int(policy["tools"]))
        response = self.router.complete(messages, schemas)
        if response.tool_calls:
            tool_messages = list(messages)
            for call in response.tool_calls:
                result = self.tools.invoke(call.name, call.arguments)
                tool_messages.append(Message("tool", f"{call.name}: {result}"))
            tool_messages, schemas = self.optimizer.optimize(tool_messages, self.tools.schemas(),
                                                              max_chars=int(policy["context_chars"]), max_tools=int(policy["tools"]))
            response = self.router.complete(tool_messages, schemas)

        if response.text.strip():
            self.memory.remember(f"User: {user_text}\nAssistant: {response.text}")
        return response.text
