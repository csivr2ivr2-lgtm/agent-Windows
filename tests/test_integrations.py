import unittest

from agent_windows.integrations import integration_matrix


class IntegrationMatrixTests(unittest.TestCase):
    def test_all_fifteen_projects_are_tracked(self):
        rows = integration_matrix()
        self.assertEqual(len(rows), 15)
        names = {row.component for row in rows}
        expected = {
            "llmfit", "Unsloth", "Needle", "Soup", "LiveKit Agents", "OpenViking",
            "Hermes Agent", "OpenHuman", "Ponytail", "OmniRoute", "Prime Agent",
            "Firecrawl", "Wigolo", "Microsoft UFO²", "Windows-Use",
        }
        self.assertEqual(names, expected)

    def test_every_project_has_real_execution_path_and_no_pending_scaffold(self):
        rows = integration_matrix()
        for row in rows:
            with self.subTest(name=row.component):
                self.assertTrue(row.execution_path)
                self.assertFalse(row.status.startswith("PENDING"))

    def test_python_ports_and_internal_capabilities_are_active(self):
        by_name = {row.component: row for row in integration_matrix()}
        for name in ("llmfit", "Hermes Agent", "OpenHuman", "Ponytail", "OmniRoute", "Prime Agent"):
            with self.subTest(name=name):
                self.assertEqual(by_name[name].status, "ACTIVE")


if __name__ == "__main__":
    unittest.main()
