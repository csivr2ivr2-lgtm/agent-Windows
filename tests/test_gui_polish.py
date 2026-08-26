import base64
from pathlib import Path
import importlib
import unittest

from agent_windows.orchestrator import DEFAULT_SYSTEM_PROMPT
from agent_windows.windows_subprocess import CREATE_NO_WINDOW, hidden_subprocess_kwargs


ROOT = Path(__file__).resolve().parents[1]


class GuiPolishTests(unittest.TestCase):
    def test_gui_import_and_hebrew_defaults(self):
        gui = importlib.import_module("agent_windows.desktop_gui")
        self.assertEqual(gui.APP_NAME, "ai aharon")
        self.assertEqual(gui.HEBREW_LABELS["ready"], "מוכן")
        self.assertEqual(gui.HEBREW_LABELS["listening"], "מקשיב")
        self.assertEqual(gui.HEBREW_LABELS["speaking"], "מדבר")
        self.assertNotIn("agent", gui.HEBREW_LABELS)

    def test_default_prompt_requires_hebrew_and_real_system_tools(self):
        self.assertIn("ענה תמיד בעברית", DEFAULT_SYSTEM_PROMPT)
        self.assertIn("אל תנחש שעה, תאריך או מידע מערכת", DEFAULT_SYSTEM_PROMPT)
        self.assertIn("השתמש בכלי המערכת", DEFAULT_SYSTEM_PROMPT)

    def test_windows_subprocesses_use_no_console_flag(self):
        kwargs = hidden_subprocess_kwargs(os_name="nt")
        self.assertEqual(kwargs["creationflags"] & CREATE_NO_WINDOW, CREATE_NO_WINDOW)
        self.assertEqual(hidden_subprocess_kwargs(os_name="posix"), {})

    def test_installers_use_pythonw_new_shortcut_and_icon(self):
        for script_name in ("install-gui.ps1", "install-service.ps1"):
            script = (ROOT / "scripts" / script_name).read_text(encoding="utf-8")
            self.assertIn("pythonw.exe", script)
            self.assertIn("ai aharon.lnk", script)
            self.assertIn("ai-aharon.ico", script)
            self.assertIn("$shortcut.IconLocation", script)
            if script_name == "install-service.ps1":
                self.assertIn("$ServiceName = 'AgentWindowsAI'", script)

    def test_icon_is_stored_as_valid_base64_not_committed_binary(self):
        icon = ROOT / "assets" / "ai-aharon.ico"
        encoded_icon = ROOT / "assets" / "ai-aharon.ico.b64"
        self.assertFalse(icon.exists())
        decoded = base64.b64decode(
            encoded_icon.read_text(encoding="ascii").strip(), validate=True
        )
        self.assertEqual(decoded[:4], b"\x00\x00\x01\x00")
        self.assertGreater(len(decoded), 0)

    def test_installers_materialize_icon_bytes(self):
        for script_name in ("install-gui.ps1", "install-service.ps1"):
            script = (ROOT / "scripts" / script_name).read_text(encoding="utf-8")
            self.assertIn("[Convert]::FromBase64String", script)
            self.assertIn("[IO.File]::WriteAllBytes($IconPath, $bytes)", script)


if __name__ == "__main__":
    unittest.main()
