from __future__ import annotations

from .contracts import MemoryStore, Message
from .router import LLMRouter
from .tools import ToolRegistry


class AgentOrchestrator:
    def __init__(self, router: LLMRouter, memory: MemoryStore, tools: ToolRegistry) -> None:
        self.router = router
        self.memory = memory
        self.tools = tools

    def handle_text(self, user_text: str) -> str:
        context = self.memory.search(user_text)
        messages = []
        if context:
            messages.append(Message("system", "Relevant memory:\n" + "\n".join(context)))
        messages.append(Message("user", user_text))

        response = self.router.complete(messages, self.tools.schemas())
        if response.tool_calls:
            tool_messages = list(messages)
            for call in response.tool_calls:
                result = self.tools.invoke(call.name, call.arguments)
                tool_messages.append(Message("tool", f"{call.name}: {result}"))
            response = self.router.complete(tool_messages, self.tools.schemas())

        if response.text.strip():
            self.memory.remember(f"User: {user_text}\nAssistant: {response.text}")
        return response.text

