import tempfile
import unittest
from pathlib import Path

from agent_windows.agent_loop import AgentBudget, AgentLoop
from agent_windows.contracts import LLMResponse
from agent_windows.hermes_skills import HermesSkillStore
from agent_windows.memory import InMemoryStore
from agent_windows.openhuman_goals import OpenHumanGoalStore
from agent_windows.router import LLMRouter
from agent_windows.tools import ToolRegistry


class Provider:
    name = "fake"

    def __init__(self):
        self.messages = []

    def is_available(self):
        return True

    def complete(self, messages, tools):
        self.messages = list(messages)
        return LLMResponse(text="ok", provider=self.name)


class AgentContextCapabilitiesTests(unittest.TestCase):
    def test_hermes_skill_and_openhuman_goal_are_injected(self):
        with tempfile.TemporaryDirectory() as directory:
            skills = HermesSkillStore(Path(directory) / "skills")
            skills.create(
                "clock-helper",
                "---\nname: clock-helper\n---\nFor clock questions use current_datetime, never guess the time.",
            )
            goals = OpenHumanGoalStore(Path(directory) / "goal.json")
            goals.set("answer with verified system data", max_steps=2, max_tool_calls=1)
            provider = Provider()
            loop = AgentLoop(
                LLMRouter([provider]),
                InMemoryStore(),
                ToolRegistry([]),
                system_prompt="system",
                skill_provider=skills,
                goal_provider=goals,
            )
            result = loop.run("clock time", budget=AgentBudget(max_steps=8, max_tool_calls=12))
            self.assertEqual(result.text, "ok")
            joined = "\n".join(message.content for message in provider.messages)
            self.assertIn("clock-helper", joined)
            self.assertIn("[active_goal]", joined)
            self.assertIn("answer with verified system data", joined)


if __name__ == "__main__":
    unittest.main()
