import unittest

from agent_windows.contracts import ToolCall
from agent_windows.ponytail import PonytailReviewer, build_ponytail_tools


class PonytailTests(unittest.TestCase):
    def test_dependency_heavy_plan_crosses_complexity_threshold(self):
        reviewer = PonytailReviewer(complexity_threshold=3)
        result = reviewer.review_plan(
            "Create a new service; pip install another framework; then rewrite the adapter"
        )
        self.assertTrue(result.threshold_crossed)
        self.assertEqual(result.rung, "minimum-that-works")
        self.assertTrue(any("stdlib" in item for item in result.recommendations))

    def test_safety_is_never_removed_for_shortness(self):
        reviewer = PonytailReviewer()
        result = reviewer.review_plan("delete a credential file")
        self.assertTrue(result.safety_flags)
        self.assertIn("confirmation", result.safety_flags[0])

    def test_exact_duplicate_tool_calls_are_removed(self):
        reviewer = PonytailReviewer(complexity_threshold=2)
        calls = [
            ToolCall("read", {"path": "a"}),
            ToolCall("read", {"path": "a"}),
            ToolCall("read", {"path": "b"}),
        ]
        review = reviewer.review_tool_calls(calls)
        self.assertEqual(review.removed_duplicates, 1)
        self.assertEqual(len(review.calls), 2)
        self.assertTrue(review.threshold_crossed)

    def test_review_plan_is_a_real_read_only_tool(self):
        tool = build_ponytail_tools(PonytailReviewer())[0]
        self.assertEqual(tool.name, "review_plan")
        self.assertEqual(tool.risk, "read_only")
        result = tool.invoke({"plan": "reuse the existing parser"})
        self.assertIn("complexity", result)
        self.assertIn("rung", result)


if __name__ == "__main__":
    unittest.main()
