import inspect
import unittest

from agent_windows import desktop_gui


class PhoneGuiSmokeTests(unittest.TestCase):
    def test_voice_only_copy_and_app_identity(self):
        self.assertEqual(desktop_gui.APP_NAME, "ai aharon")
        self.assertEqual(desktop_gui.APP_USER_MODEL_ID, "ai.aharon.desktop")
        self.assertEqual(desktop_gui.HEBREW_LABELS["voice_only"], "שיחה קולית רציפה")
        self.assertIn("אין צורך ללחוץ", desktop_gui.HEBREW_LABELS["hint"])

    def test_gui_has_continuous_call_loop_and_no_push_to_talk_controls(self):
        build_source = inspect.getsource(desktop_gui.AgentDesktopApp._build_ui)
        loop_source = inspect.getsource(desktop_gui.AgentDesktopApp._call_loop)
        self.assertNotIn("ScrolledText", build_source)
        self.assertNotIn("Entry(", build_source)
        self.assertNotIn("mic_button", build_source)
        self.assertIn("while self._call_active.is_set()", loop_source)
        self.assertIn("self.runtime.voice.listen()", loop_source)
        self.assertIn("self.runtime.voice.speak(answer)", loop_source)

    def test_windows_app_identity_is_set_before_tk_root(self):
        source = inspect.getsource(desktop_gui.main)
        self.assertLess(source.index("_set_windows_app_identity()"), source.index("tk.Tk()"))


if __name__ == "__main__":
    unittest.main()
