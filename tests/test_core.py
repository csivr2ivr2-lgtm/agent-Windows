import unittest

from agent_windows.contracts import LLMResponse, Message, ToolCall
from agent_windows.errors import ProviderRateLimited
from agent_windows.memory import InMemoryStore
from agent_windows.orchestrator import AgentOrchestrator
from agent_windows.router import LLMRouter
from agent_windows.tools import ToolRegistry


class FakeProvider:
    def __init__(self, name, replies):
        self.name = name
        self.replies = iter(replies)

    def is_available(self):
        return True

    def complete(self, messages, tools):
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return reply


class EchoTool:
    name = "echo"
    description = "Echo text"
    schema = {"type": "object", "properties": {"text": {"type": "string"}}}

    def invoke(self, arguments):
        return arguments["text"]


class CoreTests(unittest.TestCase):
    def test_router_falls_back_after_rate_limit(self):
        first = FakeProvider("first", [ProviderRateLimited("quota")])
        second = FakeProvider("second", [LLMResponse(text="ok", provider="second")])
        result = LLMRouter([first, second]).complete([Message("user", "hi")], [])
        self.assertEqual(result.provider, "second")

    def test_orchestrator_executes_tool_then_returns_answer(self):
        provider = FakeProvider("fake", [
            LLMResponse(tool_calls=[ToolCall("echo", {"text": "done"})]),
            LLMResponse(text="done", provider="fake"),
        ])
        agent = AgentOrchestrator(LLMRouter([provider]), InMemoryStore(), ToolRegistry([EchoTool()]))
        self.assertEqual(agent.handle_text("run it"), "done")


if __name__ == "__main__":
    unittest.main()

