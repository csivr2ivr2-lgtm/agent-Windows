from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class GuiUserPathTests(unittest.TestCase):
    def test_gui_hint_text_is_removed(self):
        source = (ROOT / "src" / "agent_windows" / "desktop_gui.py").read_text(encoding="utf-8")
        self.assertNotIn("דבר כרגיל. אין צורך ללחוץ על כפתור.", source)

    def test_gui_installer_sets_ai_aharon_user_path_variable(self):
        script = (ROOT / "scripts" / "install-gui.ps1").read_text(encoding="utf-8")
        self.assertIn("SetEnvironmentVariable('AI-AHARON', $Root, 'User')", script)
        self.assertIn('cd /d "%AI-AHARON%"', script)


if __name__ == "__main__":
    unittest.main()
