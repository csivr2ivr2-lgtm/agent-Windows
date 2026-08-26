import unittest

from agent_windows.agent_loop import AgentLoop
from agent_windows.contracts import LLMResponse, ToolCall
from agent_windows.memory import InMemoryStore
from agent_windows.needle_integration import NeedlePlan, NeedleToolPlanner
from agent_windows.router import LLMRouter
from agent_windows.tools import ToolRegistry
from agent_windows.windows_tools import FunctionTool


class FakeNeedle:
    created = []
    response = {
        "type": "call",
        "confidence": 0.91,
        "function_calls": [{"name": "clock", "arguments": {}}],
    }

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.__class__.created.append(kwargs)

    def complete(self, query):
        self.query = query
        return self.__class__.response


class FakeNeedleModule:
    Needle = FakeNeedle


class FakeProvider:
    name = "fake"

    def __init__(self):
        self.messages = None

    def is_available(self):
        return True

    def complete(self, messages, tools):
        self.messages = list(messages)
        return LLMResponse(text="בוצע", provider=self.name)


class Planner:
    def plan(self, query, tools):
        return NeedlePlan((ToolCall("clock", {}),), 0.9, True, "call", "accepted")


class NeedleIntegrationTests(unittest.TestCase):
    def test_planner_returns_only_known_high_confidence_calls(self):
        FakeNeedle.created.clear()
        planner = NeedleToolPlanner(
            module_loader=lambda: FakeNeedleModule,
            confidence_threshold=0.8,
        )
        plan = planner.plan(
            "what time is it",
            [{"name": "clock", "description": "clock", "parameters": {"type": "object"}}],
        )
        self.assertTrue(plan.accepted)
        self.assertEqual(plan.calls, (ToolCall("clock", {}),))
        self.assertEqual(plan.confidence, 0.91)
        self.assertEqual(FakeNeedle.created[0]["tools"][0]["name"], "clock")

    def test_low_confidence_call_is_not_accepted(self):
        original = FakeNeedle.response
        FakeNeedle.response = {
            "type": "call",
            "confidence": 0.2,
            "function_calls": [{"name": "clock", "arguments": {}}],
        }
        try:
            planner = NeedleToolPlanner(
                module_loader=lambda: FakeNeedleModule,
                confidence_threshold=0.8,
            )
            plan = planner.plan("x", [{"name": "clock", "parameters": {}}])
            self.assertFalse(plan.accepted)
            self.assertEqual(plan.calls, ())
        finally:
            FakeNeedle.response = original

    def test_agent_loop_executes_needle_call_through_normal_tool_path(self):
        calls = []
        tool = FunctionTool(
            "clock",
            "clock",
            {"type": "object", "properties": {}},
            lambda args: calls.append(dict(args)) or "12:34",
            risk="read_only",
        )
        provider = FakeProvider()
        loop = AgentLoop(
            LLMRouter([provider]),
            InMemoryStore(),
            ToolRegistry([tool]),
            system_prompt="system",
            tool_planner=Planner(),
        )
        result = loop.run("time")
        self.assertEqual(result.text, "בוצע")
        self.assertEqual(calls, [{}])
        self.assertTrue(any("Needle preplan clock: 12:34" in m.content for m in provider.messages))


if __name__ == "__main__":
    unittest.main()
