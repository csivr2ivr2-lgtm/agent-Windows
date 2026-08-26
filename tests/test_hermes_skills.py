import tempfile
import unittest
from pathlib import Path

from agent_windows.hermes_skills import HermesSkillStore, build_hermes_skill_tools


class HermesSkillTests(unittest.TestCase):
    def test_create_read_search_delete_skill(self):
        with tempfile.TemporaryDirectory() as directory:
            store = HermesSkillStore(Path(directory) / "skills")
            doc = store.create(
                "clock-helper",
                "---\nname: clock-helper\ndescription: Read the real Windows clock\n---\nUse current_datetime for time questions.",
            )
            self.assertTrue(Path(doc.path).is_file())
            self.assertIn("clock-helper", store.list())
            self.assertIn("current_datetime", store.read("clock-helper").content)
            self.assertEqual(store.search("clock time")[0].name, "clock-helper")
            self.assertTrue(store.delete("clock-helper"))
            self.assertEqual(store.list(), [])

    def test_name_cannot_escape_store(self):
        with tempfile.TemporaryDirectory() as directory:
            store = HermesSkillStore(directory)
            with self.assertRaises(ValueError):
                store.create("../escape", "x" * 30)

    def test_mutating_skill_tools_are_guarded_by_risk(self):
        with tempfile.TemporaryDirectory() as directory:
            tools = {tool.name: tool for tool in build_hermes_skill_tools(HermesSkillStore(directory))}
            self.assertEqual(tools["skills_list"].risk, "read_only")
            self.assertEqual(tools["skills_create"].risk, "medium")
            self.assertEqual(tools["skills_delete"].risk, "high")


if __name__ == "__main__":
    unittest.main()
