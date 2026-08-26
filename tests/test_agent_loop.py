import unittest

from agent_windows.agent_loop import AgentBudget, AgentLoop, AgentState
from agent_windows.contracts import LLMResponse, ToolCall
from agent_windows.memory import InMemoryStore
from agent_windows.policy import PolicyEngine, RiskLevel
from agent_windows.router import LLMRouter
from agent_windows.tools import ToolRegistry


class FakeProvider:
    name = "fake"

    def __init__(self, replies):
        self.replies = iter(replies)

    def is_available(self):
        return True

    def complete(self, messages, tools):
        return next(self.replies)


class Tool:
    description = "test tool"
    schema = {"type": "object", "properties": {}}
    risk = "read_only"

    def __init__(self, name="ok", result="done", error=None):
        self.name = name
        self.result = result
        self.error = error
        self.calls = 0

    def invoke(self, arguments):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


class AgentLoopTests(unittest.TestCase):
    def test_plan_act_verify_completes(self):
        provider = FakeProvider([
            LLMResponse(tool_calls=[ToolCall("ok", {})], provider="fake"),
            LLMResponse(text="בוצע", provider="fake"),
        ])
        tool = Tool()
        loop = AgentLoop(
            LLMRouter([provider]), InMemoryStore(), ToolRegistry([tool]), system_prompt="system"
        )
        result = loop.run("do it")
        self.assertEqual(result.state, AgentState.COMPLETE)
        self.assertEqual(result.text, "בוצע")
        self.assertEqual(result.tool_calls, 1)
        self.assertEqual(tool.calls, 1)

    def test_tool_failure_replans_and_recovers(self):
        provider = FakeProvider([
            LLMResponse(tool_calls=[ToolCall("bad", {})], provider="fake"),
            LLMResponse(text="נכשלתי אבל התאוששתי", provider="fake"),
        ])
        tool = Tool("bad", error=RuntimeError("boom"))
        result = AgentLoop(
            LLMRouter([provider]), InMemoryStore(), ToolRegistry([tool]), system_prompt="system"
        ).run("do it")
        self.assertEqual(result.state, AgentState.COMPLETE)
        self.assertEqual(result.replans, 1)

    def test_budget_stops_run(self):
        provider = FakeProvider([
            LLMResponse(tool_calls=[ToolCall("ok", {})], provider="fake"),
        ])
        result = AgentLoop(
            LLMRouter([provider]), InMemoryStore(), ToolRegistry([Tool()]), system_prompt="system"
        ).run("do it", budget=AgentBudget(max_steps=1, max_tool_calls=1, max_replans=0))
        self.assertEqual(result.state, AgentState.FAILED)
        self.assertIn("budget", result.text.lower())

    def test_medium_risk_requires_matching_confirmation(self):
        tool = Tool()
        tool.risk = "medium"
        policy = PolicyEngine(confirmation_at=RiskLevel.MEDIUM)
        denied = policy.evaluate(tool, {})
        self.assertTrue(denied.requires_confirmation)
        self.assertFalse(denied.allowed)
        grant = policy.grant(tool.name, {})
        allowed = policy.evaluate(tool, {}, grant=grant)
        self.assertTrue(allowed.allowed)


if __name__ == "__main__":
    unittest.main()
