import tempfile
import unittest
from pathlib import Path

from agent_windows.openhuman_goals import OpenHumanGoalStore, build_openhuman_goal_tools


class OpenHumanGoalTests(unittest.TestCase):
    def test_goal_lifecycle_and_budget_constraint(self):
        with tempfile.TemporaryDirectory() as directory:
            store = OpenHumanGoalStore(Path(directory) / "goal.json")
            goal = store.set("finish the report", max_steps=5, max_tool_calls=6)
            self.assertEqual(goal.status, "active")
            self.assertIn("finish the report", store.context())
            self.assertEqual(store.constrain(8, 12), (5, 6))
            completed = store.transition("complete")
            self.assertEqual(completed.goal_id, goal.goal_id)
            self.assertEqual(store.context(), "")
            self.assertEqual(store.constrain(8, 12), (8, 12))

    def test_tools_are_operational(self):
        with tempfile.TemporaryDirectory() as directory:
            store = OpenHumanGoalStore(Path(directory) / "goal.json")
            tools = {tool.name: tool for tool in build_openhuman_goal_tools(store)}
            result = tools["goal_set"].invoke({"objective": "test", "max_steps": 3})
            self.assertEqual(result["objective"], "test")
            self.assertEqual(tools["goal_get"].invoke({})["max_steps"], 3)
            self.assertEqual(tools["goal_complete"].invoke({})["status"], "complete")


if __name__ == "__main__":
    unittest.main()
