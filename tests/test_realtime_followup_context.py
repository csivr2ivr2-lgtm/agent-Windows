import unittest

from agent_windows.contracts import Message
from agent_windows.realtime import _contextualize_followup


class RealtimeFollowupContextTests(unittest.TestCase):
    def test_short_followup_is_bound_to_previous_topic(self):
        history = (
            Message("user", "תסביר לי מה זה MCP"),
            Message("assistant", "MCP הוא פרוטוקול שמחבר מודלים לכלים."),
        )
        prompt = _contextualize_followup("משפט אחד", history)
        self.assertIn("שיחה הקודמת", prompt)
        self.assertIn("נושא האחרון", prompt)
        self.assertIn("משפט אחד", prompt)
        self.assertIn("אל תעבור להסביר מי אתה", prompt)

    def test_standalone_question_is_unchanged(self):
        text = "מי אתה?"
        self.assertEqual(_contextualize_followup(text, (Message("user", "שלום"),)), text)


if __name__ == "__main__":
    unittest.main()
